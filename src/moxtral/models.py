from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypeAlias

from moxtral.errors import InputError

TableFormat: TypeAlias = Literal["markdown", "html"]
Confidence: TypeAlias = Literal["page", "word"]
TimestampGranularity: TypeAlias = Literal["segment", "word"]
JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONMapping: TypeAlias = Mapping[str, JSONValue]

_PAGE_TOKEN = re.compile(r"[0-9]+(?:-[0-9]+)?")
_TABLE_FORMATS: tuple[TableFormat, ...] = ("markdown", "html")
_CONFIDENCE_VALUES: tuple[Confidence, ...] = ("page", "word")
_TIMESTAMP_VALUES: tuple[TimestampGranularity, ...] = ("segment", "word")

DEFAULT_RETRIES = 3


class SourceKind(StrEnum):
    FILE = "file"
    URL = "url"


class OcrSourceKind(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"


class Operation(StrEnum):
    OCR = "ocr"
    TRANSCRIPTION = "transcription"


class OutputFormat(StrEnum):
    MD = "md"
    JSON = "json"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class InputSource:
    kind: SourceKind
    value: str
    filename: str
    path: Path | None = None
    ocr_kind: OcrSourceKind = OcrSourceKind.DOCUMENT


@dataclass(frozen=True, slots=True)
class OcrRequest:
    source: InputSource
    model: str
    pages: str | None = None
    table_format: TableFormat | None = None
    extract_header: bool = False
    extract_footer: bool = False
    include_images: bool = False
    image_limit: int | None = None
    image_min_size: int | None = None
    include_blocks: bool = False
    confidence: Confidence | None = None
    retries: int = DEFAULT_RETRIES
    timeout_ms: int = 300_000


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    source: InputSource
    model: str
    language: str | None = None
    temperature: float | None = None
    diarize: bool = False
    context_bias: tuple[str, ...] = ()
    timestamps: tuple[TimestampGranularity, ...] = ()
    retries: int = DEFAULT_RETRIES
    timeout_ms: int = 300_000


@dataclass(frozen=True, slots=True)
class ApiResult:
    operation: Operation
    source: InputSource
    request_metadata: JSONMapping
    response: JSONMapping
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SavedResult:
    markdown: Path | None = None
    json: Path | None = None


def ocr_request_metadata(request: OcrRequest) -> dict[str, JSONValue]:
    """Build the JSON-safe request metadata echoed into result envelopes."""
    return {
        "model": request.model,
        "pages": request.pages,
        "table_format": request.table_format,
        "extract_header": request.extract_header,
        "extract_footer": request.extract_footer,
        "include_images": request.include_images,
        "image_limit": request.image_limit,
        "image_min_size": request.image_min_size,
        "include_blocks": request.include_blocks,
        "confidence": request.confidence,
        "retries": request.retries,
        "timeout_ms": request.timeout_ms,
    }


def transcription_request_metadata(
    request: TranscriptionRequest,
) -> dict[str, JSONValue]:
    """Build the JSON-safe request metadata echoed into result envelopes."""
    return {
        "model": request.model,
        "language": request.language,
        "temperature": request.temperature,
        "diarize": request.diarize,
        "context_bias": list(request.context_bias),
        "timestamps": list(request.timestamps),
        "retries": request.retries,
        "timeout_ms": request.timeout_ms,
    }


def _validate_model(model: str) -> None:
    if not model.strip():
        raise InputError("--model must not be blank.")


def _normalize_pages(pages: str | None) -> str | None:
    if pages is None:
        return None

    tokens = tuple(token.strip() for token in pages.strip().split(","))
    if not tokens or any(not token for token in tokens):
        raise InputError(
            "--pages must be a comma-separated list of page numbers or ranges."
        )

    for token in tokens:
        if _PAGE_TOKEN.fullmatch(token) is None:
            raise InputError(
                "--pages must contain nonnegative page numbers or ascending ranges."
            )
        number_texts = token.split("-", maxsplit=1)
        try:
            numbers = tuple(int(number) for number in number_texts)
        except ValueError as error:
            raise InputError("--pages page numbers are too large.") from error
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            raise InputError("--pages ranges must be ascending.")

    return ",".join(tokens)


def _validate_nonnegative_option(value: int | None, option: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise InputError(f"{option} must be a nonnegative integer.")


def _validate_retries(retries: int) -> None:
    if type(retries) is not int or retries < 0:
        raise InputError("--retries must be a nonnegative integer.")


def _timeout_milliseconds(timeout_seconds: float) -> int:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise InputError("--timeout must be a finite number greater than zero.")
    timeout_ms = timeout_seconds * 1000
    if not math.isfinite(timeout_ms):
        raise InputError("--timeout is too large to represent in milliseconds.")
    return math.ceil(timeout_ms)


def build_ocr_request(
    *,
    source: InputSource,
    model: str,
    pages: str | None = None,
    table_format: TableFormat | None = None,
    extract_header: bool = False,
    extract_footer: bool = False,
    include_images: bool = False,
    image_limit: int | None = None,
    image_min_size: int | None = None,
    include_blocks: bool = False,
    confidence: Confidence | None = None,
    retries: int = DEFAULT_RETRIES,
    timeout_seconds: float = 300,
) -> OcrRequest:
    _validate_model(model)
    normalized_pages = _normalize_pages(pages)

    if table_format is not None and table_format not in _TABLE_FORMATS:
        raise InputError("--table-format must be 'markdown' or 'html'.")
    if confidence is not None and confidence not in _CONFIDENCE_VALUES:
        raise InputError("--confidence must be 'page' or 'word'.")

    _validate_nonnegative_option(image_limit, "--image-limit")
    _validate_nonnegative_option(image_min_size, "--image-min-size")
    if not include_images and (image_limit is not None or image_min_size is not None):
        raise InputError("--image-limit and --image-min-size require --include-images.")
    _validate_retries(retries)

    return OcrRequest(
        source=source,
        model=model,
        pages=normalized_pages,
        table_format=table_format,
        extract_header=extract_header,
        extract_footer=extract_footer,
        include_images=include_images,
        image_limit=image_limit,
        image_min_size=image_min_size,
        include_blocks=include_blocks,
        confidence=confidence,
        retries=retries,
        timeout_ms=_timeout_milliseconds(timeout_seconds),
    )


def build_transcription_request(
    *,
    source: InputSource,
    model: str,
    language: str | None = None,
    temperature: float | None = None,
    diarize: bool = False,
    context_bias: tuple[str, ...] = (),
    timestamps: tuple[TimestampGranularity, ...] = (),
    retries: int = DEFAULT_RETRIES,
    timeout_seconds: float = 300,
) -> TranscriptionRequest:
    _validate_model(model)

    if language is not None and not language.strip():
        raise InputError("--language must not be blank.")
    if temperature is not None and not math.isfinite(temperature):
        raise InputError("--temperature must be finite.")
    if len(context_bias) > 100:
        raise InputError("--context-bias accepts at most 100 values.")
    if any(not value.strip() for value in context_bias):
        raise InputError("--context-bias values must not be blank.")
    _validate_retries(retries)

    unique_timestamps: list[TimestampGranularity] = []
    seen_timestamps: set[TimestampGranularity] = set()
    for timestamp in timestamps:
        if timestamp not in _TIMESTAMP_VALUES:
            raise InputError("--timestamps values must be 'segment' or 'word'.")
        if timestamp not in seen_timestamps:
            seen_timestamps.add(timestamp)
            unique_timestamps.append(timestamp)

    if language is not None and unique_timestamps:
        raise InputError("--language cannot be combined with --timestamps.")

    return TranscriptionRequest(
        source=source,
        model=model,
        language=language,
        temperature=temperature,
        diarize=diarize,
        context_bias=context_bias,
        timestamps=tuple(unique_timestamps),
        retries=retries,
        timeout_ms=_timeout_milliseconds(timeout_seconds),
    )
