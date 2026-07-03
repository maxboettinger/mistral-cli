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
from mistral_cli.formatters import format_ocr_markdown
from mistral_cli.mistral_client import MistralGateway
from mistral_cli.models import (
    Confidence,
    Operation,
    OutputFormat,
    TableFormat,
    build_ocr_request,
)
from mistral_cli.services.ocr import OcrGateway, OcrService
from mistral_cli.sources import resolve_source
from mistral_cli.storage import ResultStore

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext

_STDOUT_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True, slots=True)
class _Runtime:
    secrets: tuple[str, ...]
    service: OcrService
    store: ResultStore


def create_gateway(api_key: str) -> OcrGateway:
    """Create the production OCR gateway."""
    return MistralGateway(api_key)


def create_result_store() -> ResultStore:
    """Create the production result store."""
    return ResultStore()


def _nonnegative_integer(
    _context: click.Context,
    parameter: click.Parameter,
    value: int | None,
) -> int | None:
    if value is not None and value < 0:
        raise click.BadParameter(
            "must be a nonnegative integer",
            ctx=_context,
            param=parameter,
        )
    return value


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
        setup_debug_context="setting up OCR command",
        source_debug_prefix="OCR source",
        source=source,
    )


def _create_runtime(
    context: AppContext,
    secrets: tuple[str, ...],
) -> _Runtime:
    api_key, runtime_secrets = resolve_api_key(
        context,
        secrets,
        setup_debug_context="setting up OCR command",
    )
    try:
        return _Runtime(
            secrets=runtime_secrets,
            service=OcrService(create_gateway(api_key)),
            store=create_result_store(),
        )
    except Exception as error:
        _report_error(context, error, secrets=runtime_secrets)
        raise click.exceptions.Exit(1) from error


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
    callback=_nonnegative_integer,
    help="Maximum number of images to extract (requires --include-images).",
)
@click.option(
    "--image-min-size",
    type=int,
    callback=_nonnegative_integer,
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
    "--stdout",
    "write_stdout",
    is_flag=True,
    help="Also write OCR Markdown to standard output.",
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
    write_stdout: bool,
) -> None:
    """Extract readable text from local documents, images, or HTTP(S) URLs."""
    successes = 0
    failures = 0
    wrote_document = False
    runtime: _Runtime | None = None
    potential_secrets: tuple[str, ...] | None = None
    selected_table_format = (
        None if table_format == "inline" else cast(TableFormat, table_format)
    )
    selected_confidence = None if confidence == "none" else cast(Confidence, confidence)
    selected_output_format = OutputFormat(output_format)

    for source_value in sources:
        try:
            source = resolve_source(source_value, Operation.OCR)
            request = build_ocr_request(
                source=source,
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
                format_ocr_markdown(safe_result),
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
