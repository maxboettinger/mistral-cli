from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    build_ok_record,
    build_summary_record,
    serialize_ndjson,
)
from mistral_cli.models import (
    ApiResult,
    JSONValue,
    OutputFormat,
    SavedResult,
)
from mistral_cli.storage import ResultStore, get_cli_version

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext

_STDOUT_SEPARATOR = "\n\n---\n\n"

RequestT = TypeVar("RequestT")
RequestT_contra = TypeVar("RequestT_contra", contravariant=True)


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
class BatchPlan(Generic[RequestT]):
    """The operation-specific pieces the shared batch runner composes."""

    setup_debug_context: str
    source_debug_prefix: str
    build_request: Callable[[str], RequestT]
    request_metadata: Callable[[RequestT], dict[str, JSONValue]]
    create_service: Callable[[str], BatchService[RequestT]]
    create_store: Callable[[], ResultStore]
    format_markdown: Callable[[ApiResult], str]


@dataclass(frozen=True, slots=True)
class _Runtime(Generic[RequestT]):
    secrets: tuple[str, ...]
    service: BatchService[RequestT]
    store: ResultStore


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
    ) -> None:
        self._context = context
        self._plan = plan
        self._options = options
        self._successes = 0
        self._failures = 0
        self._wrote_document = False
        self._runtime: _Runtime[RequestT] | None = None
        self._potential_secrets: tuple[str, ...] | None = None

    def _secrets(self) -> tuple[str, ...]:
        if self._runtime is not None:
            return self._runtime.secrets
        if self._potential_secrets is None:
            self._potential_secrets = candidate_secrets(self._context)
        return self._potential_secrets

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
                store=self._plan.create_store(),
            )
        except Exception as error:
            self._report_failure(error, None, secrets=secrets)
            raise click.exceptions.Exit(EXIT_SETUP) from error

    def _dry_run_source(self, source_value: str, request: RequestT) -> None:
        metadata = self._plan.request_metadata(request)
        secrets = self._secrets()
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
                saved = runtime.store.save(
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
                f"Summary: {self._successes} succeeded, {self._failures} failed.\n"
            )
        if self._options.write_json_stdout:
            self._emit_record(
                build_summary_record(
                    succeeded=self._successes,
                    failed=self._failures,
                )
            )
        if self._failures:
            raise click.exceptions.Exit(EXIT_FAILURE)


def run_batch(
    context: AppContext,
    sources: tuple[str, ...],
    plan: BatchPlan[RequestT],
    options: OutputOptions,
) -> None:
    """Run a batch command loop with the shared output and error contract."""
    _validate_output_options(options)
    _BatchRun(context, plan, options).run(sources)
