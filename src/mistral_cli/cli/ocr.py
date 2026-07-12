from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from mistral_cli.cli.common import nonnegative_integer, positive_days
from mistral_cli.cli.runner import BatchPlan, DedupeOptions, OutputOptions, run_batch
from mistral_cli.formatters import format_ocr_markdown
from mistral_cli.mistral_client import MistralGateway
from mistral_cli.models import (
    DEFAULT_RETRIES,
    Confidence,
    OcrRequest,
    Operation,
    OutputFormat,
    TableFormat,
    build_ocr_request,
    ocr_request_metadata,
)
from mistral_cli.services.ocr import OcrGateway, OcrService
from mistral_cli.sources import resolve_source
from mistral_cli.storage import ResultStore

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext


def create_gateway(api_key: str) -> OcrGateway:
    """Create the production OCR gateway."""
    return MistralGateway(api_key)


def create_result_store() -> ResultStore:
    """Create the production result store."""
    return ResultStore()


@click.command()
@click.argument("sources", metavar="SOURCE...", nargs=-1, required=True)
@click.option(
    "--model",
    default="mistral-ocr-latest",
    show_default=True,
    help="OCR model.",
)
@click.option(
    "--pages",
    help="Page numbers or ranges in API syntax, such as 0,2-4.",
)
@click.option(
    "--table-format",
    type=click.Choice(["inline", "markdown", "html"]),
    default="inline",
    show_default=True,
    help="How tables are represented in the OCR response.",
)
@click.option(
    "--extract-header",
    is_flag=True,
    help="Extract page headers.",
)
@click.option(
    "--extract-footer",
    is_flag=True,
    help="Extract page footers.",
)
@click.option(
    "--include-images",
    is_flag=True,
    help="Include extracted image data in the response.",
)
@click.option(
    "--image-limit",
    type=int,
    callback=nonnegative_integer,
    help="Maximum number of images to extract (requires --include-images).",
)
@click.option(
    "--image-min-size",
    type=int,
    callback=nonnegative_integer,
    help="Minimum extracted image size (requires --include-images).",
)
@click.option(
    "--include-blocks",
    is_flag=True,
    help="Include structured OCR blocks.",
)
@click.option(
    "--confidence",
    type=click.Choice(["none", "page", "word"]),
    default="none",
    show_default=True,
    help="Confidence score granularity.",
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
    help="Also write OCR Markdown to standard output.",
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
def ocr(
    context: AppContext,
    sources: tuple[str, ...],
    model: str,
    pages: str | None,
    table_format: str,
    extract_header: bool,
    extract_footer: bool,
    include_images: bool,
    image_limit: int | None,
    image_min_size: int | None,
    include_blocks: bool,
    confidence: str,
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
    """Extract readable text from local documents, images, or HTTP(S) URLs."""
    selected_table_format = (
        None if table_format == "inline" else cast(TableFormat, table_format)
    )
    selected_confidence = None if confidence == "none" else cast(Confidence, confidence)

    def build_request(source_value: str) -> OcrRequest:
        return build_ocr_request(
            source=resolve_source(source_value, Operation.OCR),
            model=model,
            pages=pages,
            table_format=selected_table_format,
            extract_header=extract_header,
            extract_footer=extract_footer,
            include_images=include_images,
            image_limit=image_limit,
            image_min_size=image_min_size,
            include_blocks=include_blocks,
            confidence=selected_confidence,
            timeout_seconds=timeout,
            retries=retries,
        )

    run_batch(
        context,
        sources,
        BatchPlan(
            setup_debug_context="setting up OCR command",
            source_debug_prefix="OCR source",
            operation=Operation.OCR,
            build_request=build_request,
            request_metadata=ocr_request_metadata,
            create_service=lambda api_key: OcrService(create_gateway(api_key)),
            create_store=lambda: create_result_store(),
            format_markdown=format_ocr_markdown,
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
