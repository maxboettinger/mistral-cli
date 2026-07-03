from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from mistral_cli.config import ConfigStore
from mistral_cli.console import sanitize_terminal_text
from mistral_cli.errors import ConfigError, redact, translate_exception
from mistral_cli.formatters import format_transcription_markdown
from mistral_cli.mistral_client import MistralGateway
from mistral_cli.models import (
    ApiResult,
    InputSource,
    JSONMapping,
    JSONValue,
    Operation,
    OutputFormat,
    TimestampGranularity,
    build_transcription_request,
)
from mistral_cli.services.transcription import (
    TranscriptionGateway,
    TranscriptionService,
)
from mistral_cli.sources import resolve_source
from mistral_cli.storage import ResultStore

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext

_STDOUT_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True, slots=True)
class _Runtime:
    secrets: tuple[str, ...]
    service: TranscriptionService
    store: ResultStore


def create_gateway(api_key: str) -> TranscriptionGateway:
    """Create the production transcription gateway."""
    return MistralGateway(api_key)


def create_result_store() -> ResultStore:
    """Create the production result store."""
    return ResultStore()


def _safe_terminal_text(text: str, secrets: tuple[str, ...]) -> str:
    return sanitize_terminal_text(redact(text, secrets))


def _redaction_secrets(context: AppContext) -> tuple[str, ...]:
    secrets: list[str] = []
    environment_key = os.environ.get("MISTRAL_API_KEY")
    if environment_key is not None and environment_key.strip():
        secrets.append(environment_key)

    try:
        configured_key = ConfigStore(context.config_path, environ={}).load().api_key
    except ConfigError:
        configured_key = None
    if configured_key is not None:
        secrets.append(configured_key)
    return tuple(dict.fromkeys(secrets))


def _with_secret(secrets: tuple[str, ...], secret: str) -> tuple[str, ...]:
    if secret in secrets:
        return secrets
    return (*secrets, secret)


def _redact_json_value(
    value: JSONValue,
    secrets: tuple[str, ...],
) -> JSONValue:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_redact_json_value(item, secrets) for item in value]
    if isinstance(value, Mapping):
        return _redact_json_mapping(cast(JSONMapping, value), secrets)
    return value


def _redact_json_mapping(
    mapping: JSONMapping,
    secrets: tuple[str, ...],
) -> dict[str, JSONValue]:
    redacted_mapping: dict[str, JSONValue] = {}
    for key, value in mapping.items():
        safe_key = redact(key, secrets)
        unique_key = safe_key
        suffix = 1
        while unique_key in redacted_mapping:
            unique_key = f"{safe_key}#{suffix}"
            suffix += 1
        redacted_mapping[unique_key] = _redact_json_value(value, secrets)
    return redacted_mapping


def _redact_source(
    source: InputSource,
    secrets: tuple[str, ...],
) -> InputSource:
    safe_path = None if source.path is None else Path(redact(str(source.path), secrets))
    return InputSource(
        kind=source.kind,
        value=redact(source.value, secrets),
        filename=redact(source.filename, secrets),
        path=safe_path,
        ocr_kind=source.ocr_kind,
    )


def _redact_result(
    result: ApiResult,
    secrets: tuple[str, ...],
) -> ApiResult:
    return ApiResult(
        operation=result.operation,
        source=_redact_source(result.source, secrets),
        request_metadata=_redact_json_mapping(result.request_metadata, secrets),
        response=_redact_json_mapping(result.response, secrets),
        created_at=result.created_at,
    )


def _report_error(
    context: AppContext,
    error: Exception,
    *,
    secrets: tuple[str, ...],
    source: str | None = None,
) -> None:
    translated = translate_exception(error)
    line = (
        f"Setup error: {translated}\n"
        if source is None
        else f"{source}: {translated}\n"
    )
    debug_context = (
        "setting up transcription command"
        if source is None
        else f"Transcription source: {source}"
    )
    context.consoles.write_stderr(_safe_terminal_text(line, secrets))
    if context.debug:
        context.consoles.print_debug_exception(
            error,
            secrets=secrets,
            context=debug_context,
        )


def _create_runtime(
    context: AppContext,
    secrets: tuple[str, ...],
) -> _Runtime:
    try:
        api_key = ConfigStore(context.config_path).resolve_api_key()
    except ConfigError as error:
        _report_error(context, error, secrets=secrets)
        raise click.exceptions.Exit(1) from error

    runtime_secrets = _with_secret(secrets, api_key)
    try:
        return _Runtime(
            secrets=runtime_secrets,
            service=TranscriptionService(create_gateway(api_key)),
            store=create_result_store(),
        )
    except Exception as error:
        _report_error(context, error, secrets=runtime_secrets)
        raise click.exceptions.Exit(1) from error


@click.command()
@click.argument("sources", metavar="SOURCE...", nargs=-1, required=True)
@click.option(
    "--model",
    default="voxtral-mini-latest",
    show_default=True,
    help="Audio transcription model.",
)
@click.option(
    "--language",
    help="Language code for the source audio.",
)
@click.option(
    "--temperature",
    type=float,
    help="Sampling temperature.",
)
@click.option(
    "--diarize",
    is_flag=True,
    help="Identify speakers in the transcription.",
)
@click.option(
    "--context-bias",
    multiple=True,
    help="Bias transcription toward this text (repeatable, maximum 100).",
)
@click.option(
    "--timestamps",
    type=click.Choice(["segment", "word"]),
    multiple=True,
    help="Timestamp granularity to request (repeatable).",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="Directory in which to save result files.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["md", "json", "both"]),
    default="both",
    show_default=True,
    help="Result file format to save.",
)
@click.option(
    "--timeout",
    type=float,
    default=300.0,
    show_default=True,
    help="Request timeout in seconds.",
)
@click.option(
    "--stdout",
    "write_stdout",
    is_flag=True,
    help="Also write transcription Markdown to standard output.",
)
@click.pass_obj
def transcribe(
    context: AppContext,
    sources: tuple[str, ...],
    model: str,
    language: str | None,
    temperature: float | None,
    diarize: bool,
    context_bias: tuple[str, ...],
    timestamps: tuple[str, ...],
    output_dir: Path | None,
    output_format: str,
    timeout: float,
    write_stdout: bool,
) -> None:
    """Transcribe local audio files or HTTP(S) URLs into text."""
    successes = 0
    failures = 0
    wrote_document = False
    runtime: _Runtime | None = None
    potential_secrets: tuple[str, ...] | None = None
    selected_timestamps = cast(tuple[TimestampGranularity, ...], timestamps)
    selected_output_format = OutputFormat(output_format)

    for source_value in sources:
        try:
            source = resolve_source(source_value, Operation.TRANSCRIPTION)
            request = build_transcription_request(
                source=source,
                model=model,
                language=language,
                temperature=temperature,
                diarize=diarize,
                context_bias=context_bias,
                timestamps=selected_timestamps,
                timeout_seconds=timeout,
            )
        except Exception as error:
            if potential_secrets is None:
                potential_secrets = _redaction_secrets(context)
            secrets = potential_secrets if runtime is None else runtime.secrets
            _report_error(
                context,
                error,
                secrets=secrets,
                source=source_value,
            )
            failures += 1
            continue

        if potential_secrets is None:
            potential_secrets = _redaction_secrets(context)
        if runtime is None:
            runtime = _create_runtime(context, potential_secrets)
        secrets = runtime.secrets
        safe_source_value = _safe_terminal_text(source_value, secrets)
        try:
            context.consoles.write_stderr(f"Processing: {safe_source_value}\n")
            result = runtime.service.run(request)
            safe_result = _redact_result(result, secrets)
            markdown = _safe_terminal_text(
                format_transcription_markdown(safe_result),
                secrets,
            )
            saved = runtime.store.save(
                safe_result,
                markdown,
                selected_output_format,
                output_dir=output_dir,
            )
        except Exception as error:
            _report_error(
                context,
                error,
                secrets=secrets,
                source=source_value,
            )
            failures += 1
            continue

        if saved.markdown is not None:
            safe_path = _safe_terminal_text(str(saved.markdown), secrets)
            context.consoles.write_stderr(f"Saved: {safe_path}\n")
        if saved.json is not None:
            safe_path = _safe_terminal_text(str(saved.json), secrets)
            context.consoles.write_stderr(f"Saved: {safe_path}\n")
        if write_stdout:
            if wrote_document:
                context.consoles.write_stdout(_STDOUT_SEPARATOR)
            context.consoles.write_stdout(markdown)
            wrote_document = True
        successes += 1

    context.consoles.write_stderr(
        f"Summary: {successes} succeeded, {failures} failed.\n"
    )
    if failures:
        raise click.exceptions.Exit(1)
