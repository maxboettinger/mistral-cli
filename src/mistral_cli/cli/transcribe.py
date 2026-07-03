from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from mistral_cli.cli.common import (
    candidate_secrets,
    redact_result,
    report_error,
    resolve_api_key,
    safe_terminal_text,
)
from mistral_cli.formatters import format_transcription_markdown
from mistral_cli.mistral_client import MistralGateway
from mistral_cli.models import (
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


def _report_error(
    context: AppContext,
    error: Exception,
    *,
    secrets: tuple[str, ...],
    source: str | None = None,
) -> None:
    report_error(
        context,
        error,
        secrets=secrets,
        setup_debug_context="setting up transcription command",
        source_debug_prefix="Transcription source",
        source=source,
    )


def _create_runtime(
    context: AppContext,
    secrets: tuple[str, ...],
) -> _Runtime:
    api_key, runtime_secrets = resolve_api_key(
        context,
        secrets,
        setup_debug_context="setting up transcription command",
    )
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
    type=click.Choice(["segment", "word"], case_sensitive=False),
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
                potential_secrets = candidate_secrets(context)
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
            potential_secrets = candidate_secrets(context)
        if runtime is None:
            runtime = _create_runtime(context, potential_secrets)
        secrets = runtime.secrets
        safe_source_value = safe_terminal_text(source_value, secrets)
        try:
            context.consoles.write_stderr(f"Processing: {safe_source_value}\n")
            result = runtime.service.run(request)
            safe_result = redact_result(result, secrets)
            markdown = safe_terminal_text(
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
            safe_path = safe_terminal_text(str(saved.markdown), secrets)
            context.consoles.write_stderr(f"Saved: {safe_path}\n")
        if saved.json is not None:
            safe_path = safe_terminal_text(str(saved.json), secrets)
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
