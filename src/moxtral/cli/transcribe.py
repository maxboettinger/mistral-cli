from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from moxtral.cli.common import nonnegative_integer, positive_days
from moxtral.cli.runner import BatchPlan, DedupeOptions, OutputOptions, run_batch
from moxtral.formatters import format_transcription_markdown
from moxtral.mistral_client import MistralGateway
from moxtral.models import (
    DEFAULT_RETRIES,
    Operation,
    OutputFormat,
    TimestampGranularity,
    TranscriptionRequest,
    build_transcription_request,
    transcription_request_metadata,
)
from moxtral.services.transcription import (
    TranscriptionGateway,
    TranscriptionService,
)
from moxtral.sources import resolve_source
from moxtral.storage import ResultStore

if TYPE_CHECKING:
    from moxtral.cli.main import AppContext


def create_gateway(api_key: str) -> TranscriptionGateway:
    """Create the production transcription gateway."""
    return MistralGateway(api_key)


def create_result_store() -> ResultStore:
    """Create the production result store."""
    return ResultStore()


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
    "--retries",
    type=int,
    default=DEFAULT_RETRIES,
    show_default=True,
    callback=nonnegative_integer,
    help=(
        "Retry attempts for rate-limited, server-error, and connection "
        "failures (0 disables)."
    ),
)
@click.option(
    "--stdout",
    "write_stdout",
    is_flag=True,
    help="Also write transcription Markdown to standard output.",
)
@click.option(
    "--json",
    "write_json",
    is_flag=True,
    help="Write NDJSON result records to standard output (one per source).",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress progress and summary output.",
)
@click.option(
    "--no-save",
    is_flag=True,
    help="Do not save result files (requires --json or --stdout).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate sources and options without calling the API.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Process the source even if an identical recent result exists.",
)
@click.option(
    "--dedupe-window",
    type=float,
    default=30.0,
    show_default=True,
    metavar="DAYS",
    callback=positive_days,
    help="Look-back window in days for skipping identical, already-saved results.",
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
    retries: int,
    write_stdout: bool,
    write_json: bool,
    quiet: bool,
    no_save: bool,
    dry_run: bool,
    force: bool,
    dedupe_window: float,
) -> None:
    """Transcribe local audio files or HTTP(S) URLs into text."""
    selected_timestamps = cast("tuple[TimestampGranularity, ...]", timestamps)

    def build_request(source_value: str) -> TranscriptionRequest:
        return build_transcription_request(
            source=resolve_source(source_value, Operation.TRANSCRIPTION),
            model=model,
            language=language,
            temperature=temperature,
            diarize=diarize,
            context_bias=context_bias,
            timestamps=selected_timestamps,
            timeout_seconds=timeout,
            retries=retries,
        )

    run_batch(
        context,
        sources,
        BatchPlan(
            setup_debug_context="setting up transcription command",
            source_debug_prefix="Transcription source",
            operation=Operation.TRANSCRIPTION,
            build_request=build_request,
            request_metadata=transcription_request_metadata,
            create_service=lambda api_key: TranscriptionService(
                create_gateway(api_key)
            ),
            create_store=lambda: create_result_store(),
            format_markdown=format_transcription_markdown,
        ),
        OutputOptions(
            output_format=OutputFormat(output_format),
            output_dir=output_dir,
            write_markdown_stdout=write_stdout,
            write_json_stdout=write_json,
            quiet=quiet,
            no_save=no_save,
            dry_run=dry_run,
        ),
        DedupeOptions(force=force, window_days=dedupe_window),
    )
