import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from moxtral.console import sanitize_terminal_text
from moxtral.formatters import (
    RECORD_SCHEMA_VERSION,
    build_dry_run_record,
    build_envelope,
    build_error_record,
    build_existing_result,
    build_ok_record,
    build_skipped_record,
    build_summary_record,
    format_ocr_markdown,
    format_timestamp,
    format_transcription_markdown,
    serialize_json,
    serialize_ndjson,
)
from moxtral.models import (
    ApiResult,
    InputSource,
    JSONMapping,
    Operation,
    SourceKind,
)


def make_result(
    *,
    operation: Operation = Operation.OCR,
    response: JSONMapping,
    request: JSONMapping | None = None,
) -> ApiResult:
    return ApiResult(
        operation=operation,
        source=InputSource(
            kind=SourceKind.FILE,
            value="/private/secret/über.pdf",
            filename="über.pdf",
            path=Path("/private/secret/über.pdf"),
        ),
        request_metadata=request or {"model": "requested-model"},
        response=response,
        created_at=datetime(
            2026,
            7,
            2,
            14,
            34,
            56,
            123456,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00.000"),
        (65.125, "00:01:05.125"),
        (25 * 3600 + 2.0039, "25:00:02.004"),
    ],
)
def test_format_timestamp_uses_total_hours_and_milliseconds(
    seconds: float,
    expected: str,
) -> None:
    assert format_timestamp(seconds) == expected


@pytest.mark.parametrize(
    "seconds",
    [-1, float("nan"), float("inf"), cast(float, "not-a-number")],
)
def test_format_timestamp_rejects_malformed_api_values(seconds: float) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        format_timestamp(seconds)


def test_ocr_markdown_preserves_page_order_and_adjacent_header_footer() -> None:
    result = make_result(
        response={
            "model": "effective-model",
            "pages": [
                {
                    "index": 1,
                    "header": "Second header",
                    "markdown": "Second **body**",
                    "footer": "Second footer",
                },
                {
                    "index": 0,
                    "header": "",
                    "markdown": "First body\n\nwith spacing",
                    "footer": None,
                },
            ],
        }
    )

    rendered = format_ocr_markdown(result)

    assert rendered.startswith(
        "# OCR Result\n\n"
        "- Created: `2026-07-02T12:34:56.123456Z`\n"
        "- Source: `über.pdf`\n"
        "- Model: `effective-model`\n"
    )
    assert rendered.index("<!-- Page 2 -->") < rendered.index("<!-- Page 1 -->")
    assert (
        "<!-- Page 2 -->\n\nSecond header\n\nSecond **body**\n\nSecond footer"
        in rendered
    )
    assert rendered.count("Second header") == 1
    assert rendered.count("Second footer") == 1
    assert "First body\n\nwith spacing" in rendered
    assert rendered.endswith("\n")


def test_ocr_markdown_handles_missing_optional_response_fields() -> None:
    result = make_result(response={}, request={"model": "fallback-model"})

    rendered = format_ocr_markdown(result)

    assert "- Model: `fallback-model`" in rendered
    assert "<!-- Page" not in rendered


def test_ocr_markdown_preserves_raw_page_header_and_footer_newlines() -> None:
    header = "\nHeader raw\n"
    markdown = "\nraw body\n"
    footer = "\nfooter raw\n"
    result = make_result(
        response={
            "pages": [
                {
                    "index": 0,
                    "header": header,
                    "markdown": markdown,
                    "footer": footer,
                }
            ]
        }
    )

    rendered = format_ocr_markdown(result)
    expected_page = "\n\n".join(["<!-- Page 1 -->", header, markdown, footer])

    assert expected_page in rendered
    assert rendered.index(header) < rendered.index(markdown) < rendered.index(footer)


def test_transcription_markdown_uses_plain_text_without_segments() -> None:
    result = make_result(
        operation=Operation.TRANSCRIPTION,
        response={"text": "Grüße aus Berlin 👋"},
    )

    rendered = format_transcription_markdown(result)

    assert rendered.startswith("# Transcription Result\n\n")
    assert "- Created: `2026-07-02T12:34:56.123456Z`" in rendered
    assert "- Source: `über.pdf`" in rendered
    assert "- Model: `requested-model`" in rendered
    assert rendered.endswith("Grüße aus Berlin 👋\n")


def test_transcription_markdown_renders_segments_speaker_and_timestamps() -> None:
    result = make_result(
        operation=Operation.TRANSCRIPTION,
        response={
            "text": "Hello there",
            "segments": [
                {
                    "start": 1,
                    "end": 2.5,
                    "speaker": "Speaker 1",
                    "text": "Hello",
                    "score": 0.99,
                },
                {"start": 2.5, "end": 3, "text": "there"},
            ],
        },
    )

    rendered = format_transcription_markdown(result)

    assert "**[00:00:01.000\N{EN DASH}00:00:02.500] Speaker 1:** Hello" in rendered
    assert "**[00:00:02.500\N{EN DASH}00:00:03.000]:** there" in rendered


def test_transcription_markdown_falls_back_when_all_segments_are_malformed() -> None:
    result = make_result(
        operation=Operation.TRANSCRIPTION,
        response={
            "text": "Do not lose me",
            "segments": [
                {"start": "bad", "end": 2, "text": "bad timestamp"},
                {"start": 3, "end": 2, "text": "backwards"},
                {"start": 3, "end": 4},
            ],
        },
    )

    assert format_transcription_markdown(result).endswith("Do not lose me\n")


def test_transcription_markdown_falls_back_if_any_segment_is_malformed() -> None:
    result = make_result(
        operation=Operation.TRANSCRIPTION,
        response={
            "text": "Complete response text",
            "segments": [
                {"start": 0, "end": 1, "text": "valid subset"},
                {"start": "bad", "end": 2, "text": "malformed"},
            ],
        },
    )

    rendered = format_transcription_markdown(result)

    assert rendered.endswith("Complete response text\n")
    assert "valid subset" not in rendered
    assert "[00:00:00.000" not in rendered


def test_transcription_markdown_falls_back_for_huge_integer_timestamp() -> None:
    huge_timestamp = 10**1000
    result = make_result(
        operation=Operation.TRANSCRIPTION,
        response={
            "text": "Complete text survives",
            "segments": [
                {
                    "start": 0,
                    "end": huge_timestamp,
                    "text": "unrepresentable segment",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="timestamp"):
        format_timestamp(huge_timestamp)
    assert format_transcription_markdown(result).endswith("Complete text survives\n")


def test_build_envelope_has_exact_safe_schema_and_plain_containers() -> None:
    request = cast(
        JSONMapping,
        {
            "model": "ocr-model",
            "options": cast(JSONMapping, {"pages": [0, 1]}),
            "api_key": "not-a-real-key",
            "config": {"profile": "private"},
        },
    )
    result = make_result(response={"pages": [{"markdown": "café"}]}, request=request)

    envelope = build_envelope(result, "9.8.7")

    assert envelope == {
        "schema_version": 1,
        "created_at": "2026-07-02T12:34:56.123456Z",
        "source": {
            "kind": "file",
            "value": "/private/secret/über.pdf",
            "filename": "über.pdf",
        },
        "request": {
            "model": "ocr-model",
            "options": {"pages": [0, 1]},
        },
        "response": {"pages": [{"markdown": "café"}]},
        "cli_version": "9.8.7",
    }
    assert type(envelope["request"]) is dict
    assert type(cast(dict[str, object], envelope["request"])["options"]) is dict
    assert "path" not in cast(dict[str, object], envelope["source"])
    assert "api_key" not in cast(dict[str, object], envelope["request"])
    assert "config" not in cast(dict[str, object], envelope["request"])
    assert result.request_metadata is request


def test_envelope_recursively_excludes_internal_request_metadata() -> None:
    request = cast(
        JSONMapping,
        {
            "model": "safe-model",
            "config_path": "/secret/config.toml",
            "nested": {
                "path": "/secret/input.pdf",
                "API-Key": "secret-api-value",
                "deeper": [
                    {
                        "mistral_api_key": "another-secret",
                        "configuration": "private-profile",
                        "language": "de",
                    }
                ],
            },
        },
    )
    response = cast(
        JSONMapping,
        {
            "path": "ordinary/response/content",
            "details": {"configuration": "returned API content"},
        },
    )

    envelope = build_envelope(
        make_result(response=response, request=request),
        "1.0",
    )
    serialized = serialize_json(envelope)

    assert envelope["request"] == {
        "model": "safe-model",
        "nested": {"deeper": [{"language": "de"}]},
    }
    assert envelope["response"] == {
        "path": "ordinary/response/content",
        "details": {"configuration": "returned API content"},
    }
    for secret in (
        "config_path",
        "/secret/config.toml",
        "/secret/input.pdf",
        "secret-api-value",
        "another-secret",
        "private-profile",
    ):
        assert secret not in serialized


def test_envelope_excludes_source_path_without_leaking_unrelated_api_state() -> None:
    result = make_result(response={"text": "safe"}, request={"model": "audio"})

    serialized = serialize_json(build_envelope(result, "1.0"))

    assert "/private/secret/über.pdf" in serialized
    assert '"path"' not in serialized
    assert "MISTRAL_API_KEY" not in serialized
    assert serialized.endswith("\n")
    assert "\\u00fc" not in serialized


def test_json_serialization_is_strict() -> None:
    with pytest.raises(ValueError):
        serialize_json({"not_json": float("nan")})


def test_serialize_ndjson_is_single_ascii_line() -> None:
    payload = {"text": "café \x1b[31mred\x1b[0m \x9b31m", "n": 1}
    line = serialize_ndjson(payload)

    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    assert line == line.encode("ascii", errors="strict").decode("ascii")
    assert json.loads(line) == payload


def test_serialize_ndjson_survives_terminal_sanitization() -> None:
    payload = {"text": "\x1b]0;evil\x07 \x9b31m plain"}
    line = serialize_ndjson(payload)

    assert sanitize_terminal_text(line) == line
    assert json.loads(sanitize_terminal_text(line)) == payload


def test_serialize_ndjson_rejects_nan() -> None:
    with pytest.raises(ValueError):
        serialize_ndjson({"value": float("nan")})


def test_build_ok_record_shape() -> None:
    record = build_ok_record(
        source="doc.pdf",
        envelope={"schema_version": 1},
        saved_markdown="/tmp/a.md",
        saved_json=None,
    )
    assert record == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "ok",
        "source": "doc.pdf",
        "envelope": {"schema_version": 1},
        "saved": {"markdown": "/tmp/a.md", "json": None},
    }


def test_build_error_record_shape() -> None:
    record = build_error_record(
        source=None,
        code="config_error",
        message="No API key configured.",
        status_code=None,
    )
    assert record == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "error",
        "source": None,
        "error": {
            "code": "config_error",
            "message": "No API key configured.",
            "status_code": None,
        },
    }


def test_build_dry_run_record_strips_sensitive_request_keys() -> None:
    record = build_dry_run_record(
        source="doc.pdf",
        request_metadata={"model": "mistral-ocr-latest", "api_key": "secret"},
    )
    assert record == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "dry_run",
        "source": "doc.pdf",
        "request": {"model": "mistral-ocr-latest"},
    }


def test_build_summary_record_shape() -> None:
    assert build_summary_record(succeeded=2, failed=1, skipped=3) == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "summary",
        "succeeded": 2,
        "failed": 1,
        "skipped": 3,
    }


def test_build_existing_result_shape() -> None:
    saved_at = datetime(2026, 7, 3, 12, 13, 14, tzinfo=UTC)
    record = build_existing_result(
        saved_at=saved_at,
        markdown="/tmp/a.md",
        json_path="/tmp/a.json",
        model="mistral-ocr-latest",
    )
    assert record == {
        "saved_at": "2026-07-03T12:13:14Z",
        "markdown": "/tmp/a.md",
        "json": "/tmp/a.json",
        "model": "mistral-ocr-latest",
    }


def test_build_skipped_record_shape() -> None:
    existing = build_existing_result(
        saved_at=datetime(2026, 7, 3, tzinfo=UTC),
        markdown="/tmp/a.md",
        json_path=None,
        model="mistral-ocr-latest",
    )
    record = build_skipped_record(source="doc.pdf", existing=existing)
    assert record == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "skipped",
        "source": "doc.pdf",
        "reason": "duplicate",
        "existing": existing,
    }


def test_build_dry_run_record_includes_duplicate_only_when_given() -> None:
    without_duplicate = build_dry_run_record(
        source="doc.pdf",
        request_metadata={"model": "mistral-ocr-latest"},
    )
    assert "duplicate" not in without_duplicate

    duplicate = build_existing_result(
        saved_at=datetime(2026, 7, 3, tzinfo=UTC),
        markdown="/tmp/a.md",
        json_path=None,
        model="mistral-ocr-latest",
    )
    with_duplicate = build_dry_run_record(
        source="doc.pdf",
        request_metadata={"model": "mistral-ocr-latest"},
        duplicate=duplicate,
    )
    assert with_duplicate == {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "dry_run",
        "source": "doc.pdf",
        "request": {"model": "mistral-ocr-latest"},
        "duplicate": duplicate,
    }
