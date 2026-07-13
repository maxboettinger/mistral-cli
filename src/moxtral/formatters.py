from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

from moxtral.models import ApiResult, JSONMapping, JSONValue

_TIMESTAMP_SEPARATOR = "\N{EN DASH}"

RECORD_SCHEMA_VERSION = 1


def format_timestamp(seconds: float) -> str:
    """Format a nonnegative API timestamp with millisecond precision."""
    if type(seconds) not in (int, float):
        raise ValueError("timestamp must be a finite nonnegative number")
    try:
        is_finite = math.isfinite(seconds)
    except OverflowError as error:
        raise ValueError("timestamp is too large") from error
    if not is_finite or seconds < 0:
        raise ValueError("timestamp must be a finite nonnegative number")

    milliseconds_value = seconds * 1000
    try:
        milliseconds_are_finite = math.isfinite(milliseconds_value)
    except OverflowError as error:
        raise ValueError("timestamp is too large") from error
    if not milliseconds_are_finite:
        raise ValueError("timestamp is too large")
    total_milliseconds = math.floor(milliseconds_value + 0.5)
    total_seconds, milliseconds = divmod(total_milliseconds, 1000)
    total_minutes, second = divmod(total_seconds, 60)
    hours, minute = divmod(total_minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}.{milliseconds:03d}"


def _canonical_datetime(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _is_internal_request_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        "apikey" in normalized
        or "config" in normalized
        or normalized == "path"
        or normalized.startswith("path")
        or normalized.endswith("path")
    )


def _model(result: ApiResult) -> str:
    response_model = _string(result.response.get("model"))
    request_model = _string(result.request_metadata.get("model"))
    return response_model or request_model or "unknown"


def _provenance(result: ApiResult, title: str) -> str:
    return (
        f"# {title}\n\n"
        f"- Created: `{_canonical_datetime(result.created_at)}`\n"
        f"- Source: `{result.source.filename}`\n"
        f"- Model: `{_model(result)}`"
    )


def _page_number(page: Mapping[str, object], position: int) -> int:
    index = page.get("index")
    if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
        return index + 1
    return position + 1


def format_ocr_markdown(result: ApiResult) -> str:
    """Render an OCR API result as deterministic Markdown."""
    sections = [_provenance(result, "OCR Result")]
    pages = result.response.get("pages")
    if isinstance(pages, list):
        for position, value in enumerate(pages):
            if not isinstance(value, Mapping):
                continue
            page = cast(Mapping[str, object], value)
            parts = [f"<!-- Page {_page_number(page, position)} -->"]
            header = _string(page.get("header"))
            markdown = _string(page.get("markdown"))
            footer = _string(page.get("footer"))
            if header is not None:
                parts.append(header)
            if markdown is not None:
                parts.append(markdown)
            if footer is not None:
                parts.append(footer)
            sections.append("\n\n".join(parts))
    return "\n\n".join(sections) + "\n"


def _segment_line(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    segment = cast(Mapping[str, object], value)
    text = _string(segment.get("text"))
    start = segment.get("start", segment.get("start_time"))
    end = segment.get("end", segment.get("end_time"))
    if text is None:
        return None
    try:
        start_text = format_timestamp(cast(float, start))
        end_text = format_timestamp(cast(float, end))
    except ValueError:
        return None
    if cast(float, end) < cast(float, start):
        return None

    speaker_value = segment.get("speaker", segment.get("speaker_id"))
    speaker = _string(speaker_value)
    speaker_text = f" {speaker}" if speaker is not None else ""
    return f"**[{start_text}{_TIMESTAMP_SEPARATOR}{end_text}]{speaker_text}:** {text}"


def format_transcription_markdown(result: ApiResult) -> str:
    """Render a transcription API result as deterministic Markdown."""
    sections = [_provenance(result, "Transcription Result")]
    segments = result.response.get("segments")
    lines: list[str] = []
    if isinstance(segments, list):
        for segment in segments:
            line = _segment_line(segment)
            if line is None:
                lines = []
                break
            lines.append(line)
    if lines:
        sections.append("\n\n".join(lines))
    else:
        text = _string(result.response.get("text"))
        if text is not None:
            sections.append(text)
    return "\n\n".join(sections) + "\n"


def _plain_json(value: object, *, omit_sensitive: bool = False) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if any(not isinstance(key, str) for key in mapping):
            raise TypeError("JSON object keys must be strings")
        return {
            cast(str, key): _plain_json(item, omit_sensitive=omit_sensitive)
            for key, item in mapping.items()
            if not (omit_sensitive and _is_internal_request_key(cast(str, key)))
        }
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return [_plain_json(item, omit_sensitive=omit_sensitive) for item in sequence]
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def build_envelope(result: ApiResult, cli_version: str) -> dict[str, JSONValue]:
    """Build the stable, public JSON result envelope."""
    return {
        "schema_version": 1,
        "created_at": _canonical_datetime(result.created_at),
        "source": {
            "kind": result.source.kind.value,
            "value": result.source.value,
            "filename": result.source.filename,
        },
        "request": _plain_json(result.request_metadata, omit_sensitive=True),
        "response": _plain_json(result.response),
        "cli_version": cli_version,
    }


def build_existing_result(
    *,
    saved_at: datetime,
    markdown: str | None,
    json_path: str | None,
    model: str | None,
) -> dict[str, JSONValue]:
    """Build the saved-result payload shared by skipped and dry-run records."""
    return {
        "saved_at": _canonical_datetime(saved_at),
        "markdown": markdown,
        "json": json_path,
        "model": model,
    }


def build_ok_record(
    *,
    source: str,
    envelope: dict[str, JSONValue],
    saved_markdown: str | None,
    saved_json: str | None,
) -> dict[str, JSONValue]:
    """Build the stdout record for a successfully processed source."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "ok",
        "source": source,
        "envelope": envelope,
        "saved": {"markdown": saved_markdown, "json": saved_json},
    }


def build_error_record(
    *,
    source: str | None,
    code: str,
    message: str,
    status_code: int | None,
) -> dict[str, JSONValue]:
    """Build the stdout record for a failed source or setup failure."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "error",
        "source": source,
        "error": {"code": code, "message": message, "status_code": status_code},
    }


def build_dry_run_record(
    *,
    source: str,
    request_metadata: JSONMapping,
    duplicate: dict[str, JSONValue] | None = None,
) -> dict[str, JSONValue]:
    """Build the stdout record for a validated source under --dry-run."""
    record: dict[str, JSONValue] = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "dry_run",
        "source": source,
        "request": _plain_json(request_metadata, omit_sensitive=True),
    }
    if duplicate is not None:
        record["duplicate"] = duplicate
    return record


def build_skipped_record(
    *,
    source: str,
    existing: dict[str, JSONValue],
) -> dict[str, JSONValue]:
    """Build the stdout record for a source skipped as a duplicate."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "skipped",
        "source": source,
        "reason": "duplicate",
        "existing": existing,
    }


def build_summary_record(
    *, succeeded: int, failed: int, skipped: int
) -> dict[str, JSONValue]:
    """Build the final stdout record of a completed run."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "summary",
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }


def serialize_ndjson(value: object) -> str:
    """Serialize one ASCII-only NDJSON record line."""
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        + "\n"
    )


def serialize_json(value: object) -> str:
    """Serialize a strict, readable UTF-8 JSON document."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
