from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

import click

from mistral_cli.cli.common import (
    candidate_secrets,
    extend_secrets,
    redact_result,
    report_error,
    safe_terminal_text,
)
from mistral_cli.config import ConfigStore
from mistral_cli.dedupe import (
    DEDUPE_INDEX_FILENAME,
    DedupeIndex,
    DedupeMatch,
    content_key,
    request_fingerprint,
)
from mistral_cli.errors import (
    EXIT_FAILURE,
    EXIT_SETUP,
    ApiError,
    ConfigError,
    error_code,
    redact,
    translate_exception,
)
from mistral_cli.formatters import (
    build_dry_run_record,
    build_envelope,
    build_error_record,
    build_existing_result,
    build_ok_record,
    build_skipped_record,
    build_summary_record,
    serialize_ndjson,
)
from mistral_cli.models import (
    ApiResult,
    InputSource,
    JSONValue,
    Operation,
    OutputFormat,
    SavedResult,
)
from mistral_cli.storage import ResultStore, get_cli_version

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext

_STDOUT_SEPARATOR = "\n\n---\n\n"


class SourcedRequest(Protocol):
    @property
    def source(self) -> InputSource: ...


RequestT = TypeVar("RequestT", bound=SourcedRequest)
RequestT_contra = TypeVar("RequestT_contra", bound=SourcedRequest, contravariant=True)


class BatchService(Protocol[RequestT_contra]):
    def run(self, request: RequestT_contra) -> ApiResult: ...


@dataclass(frozen=True, slots=True)
class OutputOptions:
    """Where and how a batch command emits its results."""

    output_format: OutputFormat
    output_dir: Path | None
    write_markdown_stdout: bool
    write_json_stdout: bool
    quiet: bool
    no_save: bool
    dry_run: bool


@dataclass(frozen=True, slots=True)
class DedupeOptions:
    """Duplicate-result skipping options shared by every batch command."""

    force: bool
    window_days: float


@dataclass(frozen=True, slots=True)
class BatchPlan(Generic[RequestT]):
    """The operation-specific pieces the shared batch runner composes."""

    setup_debug_context: str
    source_debug_prefix: str
    operation: Operation
    build_request: Callable[[str], RequestT]
    request_metadata: Callable[[RequestT], dict[str, JSONValue]]
    create_service: Callable[[str], BatchService[RequestT]]
    create_store: Callable[[], ResultStore]
    format_markdown: Callable[[ApiResult], str]


@dataclass(frozen=True, slots=True)
class _Runtime(Generic[RequestT]):
    secrets: tuple[str, ...]
    service: BatchService[RequestT]


def _validate_output_options(options: OutputOptions) -> None:
    if options.write_json_stdout and options.write_markdown_stdout:
        raise click.UsageError("--json cannot be combined with --stdout.")
    if options.no_save and not (
        options.write_json_stdout or options.write_markdown_stdout
    ):
        raise click.UsageError("--no-save requires --json or --stdout.")
    if options.no_save and options.output_dir is not None:
        raise click.UsageError("--no-save cannot be combined with --output-dir.")


class _BatchRun(Generic[RequestT]):
    def __init__(
        self,
        context: AppContext,
        plan: BatchPlan[RequestT],
        options: OutputOptions,
        dedupe: DedupeOptions,
    ) -> None:
        self._context = context
        self._plan = plan
        self._options = options
        self._dedupe = dedupe
        self._successes = 0
        self._failures = 0
        self._skipped = 0
        self._wrote_document = False
        self._runtime: _Runtime[RequestT] | None = None
        self._potential_secrets: tuple[str, ...] | None = None
        self._store_instance: ResultStore | None = None
        self._index_instance: DedupeIndex | None = None

    def _secrets(self) -> tuple[str, ...]:
        if self._runtime is not None:
            return self._runtime.secrets
        if self._potential_secrets is None:
            self._potential_secrets = candidate_secrets(self._context)
        return self._potential_secrets

    def _store(self) -> ResultStore:
        if self._store_instance is None:
            self._store_instance = self._plan.create_store()
        return self._store_instance

    def _index(self) -> DedupeIndex:
        if self._index_instance is None:
            self._index_instance = DedupeIndex(
                self._store().base_dir / DEDUPE_INDEX_FILENAME
            )
        return self._index_instance

    def _emit_record(self, record: dict[str, JSONValue]) -> None:
        self._context.consoles.write_stdout(serialize_ndjson(record))

    def _report_failure(
        self,
        error: Exception,
        source_value: str | None,
        secrets: tuple[str, ...] | None = None,
    ) -> None:
        known_secrets = self._secrets() if secrets is None else secrets
        report_error(
            self._context,
            error,
            secrets=known_secrets,
            setup_debug_context=self._plan.setup_debug_context,
            source_debug_prefix=self._plan.source_debug_prefix,
            source=source_value,
        )
        if self._options.write_json_stdout:
            translated = translate_exception(error)
            status_code = (
                translated.status_code if isinstance(translated, ApiError) else None
            )
            self._emit_record(
                build_error_record(
                    source=(
                        None
                        if source_value is None
                        else redact(source_value, known_secrets)
                    ),
                    code=error_code(translated),
                    message=redact(str(translated), known_secrets),
                    status_code=status_code,
                )
            )

    def _create_runtime(self) -> _Runtime[RequestT]:
        try:
            api_key = ConfigStore(self._context.config_path).resolve_api_key()
        except ConfigError as error:
            self._report_failure(error, None)
            raise click.exceptions.Exit(EXIT_SETUP) from error
        secrets = extend_secrets(self._secrets(), api_key)
        try:
            return _Runtime(
                secrets=secrets,
                service=self._plan.create_service(api_key),
            )
        except Exception as error:
            self._report_failure(error, None, secrets=secrets)
            raise click.exceptions.Exit(EXIT_SETUP) from error

    def _required_formats(self) -> tuple[bool, bool]:
        """Return (require_markdown, require_json) artifact coverage for this run."""
        if self._options.no_save:
            return self._options.write_markdown_stdout, False
        output_format = self._options.output_format
        require_markdown = (
            output_format in (OutputFormat.MD, OutputFormat.BOTH)
            or self._options.write_markdown_stdout
        )
        require_json = output_format in (OutputFormat.JSON, OutputFormat.BOTH)
        return require_markdown, require_json

    def _find_duplicate(self, *, key: str, fingerprint: str) -> DedupeMatch | None:
        require_markdown, require_json = self._required_formats()
        try:
            return self._index().lookup(
                operation=self._plan.operation,
                content_key=key,
                request_fingerprint=fingerprint,
                window_days=self._dedupe.window_days,
                require_markdown=require_markdown,
                require_json=require_json,
            )
        except OSError as error:
            if not self._options.quiet:
                self._context.consoles.write_stderr(
                    safe_terminal_text(
                        f"Warning: duplicate check unavailable: {error}; "
                        "processing anyway.\n",
                        self._secrets(),
                    )
                )
            return None

    def _read_existing_markdown(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def _emit_duplicate_skip(
        self,
        source_value: str,
        match: DedupeMatch,
        existing_markdown: str | None,
        secrets: tuple[str, ...],
    ) -> None:
        saved_at_text = (
            match.saved_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
        if not self._options.quiet:
            line = (
                f"Skipping duplicate: {source_value} "
                f"(existing result from {saved_at_text}; use --force to reprocess).\n"
            )
            self._context.consoles.write_stderr(safe_terminal_text(line, secrets))
            for path in (match.markdown, match.json):
                if path is not None:
                    safe_path = safe_terminal_text(str(path), secrets)
                    self._context.consoles.write_stderr(f"Existing: {safe_path}\n")
        if self._options.write_markdown_stdout and existing_markdown is not None:
            if self._wrote_document:
                self._context.consoles.write_stdout(_STDOUT_SEPARATOR)
            self._context.consoles.write_stdout(
                safe_terminal_text(existing_markdown, secrets)
            )
            self._wrote_document = True
        if self._options.write_json_stdout:
            self._emit_record(
                build_skipped_record(
                    source=redact(source_value, secrets),
                    existing=build_existing_result(
                        saved_at=match.saved_at,
                        markdown=(
                            None
                            if match.markdown is None
                            else redact(str(match.markdown), secrets)
                        ),
                        json_path=(
                            None
                            if match.json is None
                            else redact(str(match.json), secrets)
                        ),
                        model=(
                            None
                            if match.model is None
                            else redact(match.model, secrets)
                        ),
                    ),
                )
            )

    def _dry_run_source(self, source_value: str, request: RequestT) -> None:
        metadata = self._plan.request_metadata(request)
        secrets = self._secrets()
        match: DedupeMatch | None = None
        if not self._dedupe.force:
            try:
                key = content_key(request.source)
                fingerprint = request_fingerprint(metadata)
            except Exception as error:
                self._report_failure(error, source_value)
                self._failures += 1
                return
            match = self._find_duplicate(key=key, fingerprint=fingerprint)

        if match is not None:
            saved_at_text = (
                match.saved_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            )
            if self._options.write_json_stdout:
                self._emit_record(
                    build_dry_run_record(
                        source=redact(source_value, secrets),
                        request_metadata=metadata,
                        duplicate=build_existing_result(
                            saved_at=match.saved_at,
                            markdown=(
                                None
                                if match.markdown is None
                                else redact(str(match.markdown), secrets)
                            ),
                            json_path=(
                                None
                                if match.json is None
                                else redact(str(match.json), secrets)
                            ),
                            model=(
                                None
                                if match.model is None
                                else redact(match.model, secrets)
                            ),
                        ),
                    )
                )
            elif not self._options.quiet:
                line = (
                    f"Would skip (duplicate): {source_value} "
                    f"(existing result from {saved_at_text}).\n"
                )
                self._context.consoles.write_stderr(safe_terminal_text(line, secrets))
            self._successes += 1
            return

        if self._options.write_json_stdout:
            self._emit_record(
                build_dry_run_record(
                    source=redact(source_value, secrets),
                    request_metadata=metadata,
                )
            )
        elif not self._options.quiet:
            line = f"Would process: {source_value} (model {metadata['model']})\n"
            self._context.consoles.write_stderr(safe_terminal_text(line, secrets))
        self._successes += 1

    def _process_source(self, source_value: str, request: RequestT) -> None:
        metadata = self._plan.request_metadata(request)
        secrets = self._secrets()
        key: str | None = None
        fingerprint: str | None = None
        match: DedupeMatch | None = None
        skip_dedupe_entirely = self._options.no_save and self._dedupe.force
        try:
            if not skip_dedupe_entirely:
                key = content_key(request.source)
                fingerprint = request_fingerprint(metadata)
                if not self._dedupe.force:
                    match = self._find_duplicate(key=key, fingerprint=fingerprint)
        except Exception as error:
            self._report_failure(error, source_value)
            self._failures += 1
            return

        existing_markdown: str | None = None
        if match is not None and self._options.write_markdown_stdout:
            existing_markdown = (
                self._read_existing_markdown(match.markdown)
                if match.markdown is not None
                else None
            )
            if existing_markdown is None:
                # Recorded markdown is missing or unreadable: process normally.
                match = None

        if match is not None:
            self._emit_duplicate_skip(source_value, match, existing_markdown, secrets)
            self._skipped += 1
            return

        if self._runtime is None:
            self._runtime = self._create_runtime()
        runtime = self._runtime
        secrets = runtime.secrets
        safe_source_value = safe_terminal_text(source_value, secrets)
        try:
            if not self._options.quiet:
                self._context.consoles.write_stderr(
                    f"Processing: {safe_source_value}\n"
                )
            result = runtime.service.run(request)
            safe_result = redact_result(result, secrets)
            markdown = safe_terminal_text(
                self._plan.format_markdown(safe_result),
                secrets,
            )
            if self._options.no_save:
                saved = SavedResult()
            else:
                saved = self._store().save(
                    safe_result,
                    markdown,
                    self._options.output_format,
                    output_dir=self._options.output_dir,
                )
        except Exception as error:
            self._report_failure(error, source_value)
            self._failures += 1
            return

        if not self._options.quiet:
            for path in (saved.markdown, saved.json):
                if path is not None:
                    safe_path = safe_terminal_text(str(path), secrets)
                    self._context.consoles.write_stderr(f"Saved: {safe_path}\n")
        if self._options.write_markdown_stdout:
            if self._wrote_document:
                self._context.consoles.write_stdout(_STDOUT_SEPARATOR)
            self._context.consoles.write_stdout(markdown)
            self._wrote_document = True
        if self._options.write_json_stdout:
            record = build_ok_record(
                source=redact(source_value, secrets),
                envelope=build_envelope(safe_result, get_cli_version()),
                saved_markdown=(
                    None
                    if saved.markdown is None
                    else redact(str(saved.markdown), secrets)
                ),
                saved_json=(
                    None if saved.json is None else redact(str(saved.json), secrets)
                ),
            )
            try:
                self._emit_record(record)
            except (TypeError, ValueError) as error:
                self._report_failure(error, source_value)
                self._failures += 1
                return
        if not self._options.no_save and key is not None and fingerprint is not None:
            model_value = metadata.get("model")
            try:
                self._index().record(
                    operation=self._plan.operation,
                    content_key=key,
                    request_fingerprint=fingerprint,
                    source=request.source,
                    model=model_value if isinstance(model_value, str) else None,
                    saved=saved,
                )
            except OSError as error:
                if not self._options.quiet:
                    self._context.consoles.write_stderr(
                        safe_terminal_text(
                            "Warning: could not record result for duplicate "
                            f"detection: {error}.\n",
                            secrets,
                        )
                    )
        self._successes += 1

    def run(self, sources: tuple[str, ...]) -> None:
        for source_value in sources:
            try:
                request = self._plan.build_request(source_value)
            except Exception as error:
                self._report_failure(error, source_value)
                self._failures += 1
                continue
            if self._options.dry_run:
                self._dry_run_source(source_value, request)
            else:
                self._process_source(source_value, request)

        if not self._options.quiet:
            self._context.consoles.write_stderr(
                f"Summary: {self._successes} succeeded, {self._failures} failed, "
                f"{self._skipped} skipped.\n"
            )
        if self._options.write_json_stdout:
            self._emit_record(
                build_summary_record(
                    succeeded=self._successes,
                    failed=self._failures,
                    skipped=self._skipped,
                )
            )
        if self._failures:
            raise click.exceptions.Exit(EXIT_FAILURE)


def run_batch(
    context: AppContext,
    sources: tuple[str, ...],
    plan: BatchPlan[RequestT],
    options: OutputOptions,
    dedupe: DedupeOptions,
) -> None:
    """Run a batch command loop with the shared output and error contract."""
    _validate_output_options(options)
    _BatchRun(context, plan, options, dedupe).run(sources)
