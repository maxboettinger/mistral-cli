from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from mistral_cli.formatters import (
    build_dry_run_record,
    build_error_record,
    build_existing_result,
    build_ok_record,
    build_skipped_record,
    build_summary_record,
)
from mistral_cli.models import JSONValue
from mistral_cli.schema import record_schema

_SAMPLE_ENVELOPE: dict[str, JSONValue] = {
    "schema_version": 1,
    "created_at": "2026-07-03T12:13:14Z",
    "source": {"kind": "file", "value": "/tmp/doc.pdf", "filename": "doc.pdf"},
    "request": {"model": "mistral-ocr-latest"},
    "response": {"pages": []},
    "cli_version": "0.1.0",
}

_SAMPLE_EXISTING: dict[str, JSONValue] = build_existing_result(
    saved_at=datetime(2026, 7, 1, tzinfo=UTC),
    markdown="/tmp/existing.md",
    json_path="/tmp/existing.json",
    model="mistral-ocr-latest",
)


def _variants(schema: Mapping[str, JSONValue]) -> dict[str, Mapping[str, JSONValue]]:
    variants: dict[str, Mapping[str, JSONValue]] = {}
    for variant in cast("list[Mapping[str, JSONValue]]", schema["oneOf"]):
        properties = cast("Mapping[str, JSONValue]", variant["properties"])
        status = cast("Mapping[str, JSONValue]", properties["status"])
        variants[cast(str, status["const"])] = variant
    return variants


def test_record_schema_declares_draft_2020_12() -> None:
    schema = record_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(_variants(schema)) == {
        "ok",
        "error",
        "dry_run",
        "skipped",
        "summary",
    }


def test_record_schema_required_keys_match_builders() -> None:
    records: dict[str, dict[str, JSONValue]] = {
        "ok": build_ok_record(
            source="doc.pdf",
            envelope=_SAMPLE_ENVELOPE,
            saved_markdown=None,
            saved_json=None,
        ),
        "error": build_error_record(
            source=None,
            code="config_error",
            message="No API key configured.",
            status_code=None,
        ),
        "dry_run": build_dry_run_record(
            source="doc.pdf",
            request_metadata={"model": "mistral-ocr-latest"},
            duplicate=_SAMPLE_EXISTING,
        ),
        "skipped": build_skipped_record(
            source="doc.pdf",
            existing=_SAMPLE_EXISTING,
        ),
        "summary": build_summary_record(succeeded=1, failed=0, skipped=0),
    }

    for status, variant in _variants(record_schema()).items():
        record = records[status]
        required = cast("list[str]", variant["required"])
        properties = cast("Mapping[str, JSONValue]", variant["properties"])
        # `dry_run` declares an optional `duplicate` property, so required
        # keys are a subset of the record and the record is a subset of the
        # declared properties (equality for every other, fully-required
        # variant).
        assert set(required) <= set(record), status
        assert set(record) <= set(properties), status


def test_record_schema_describes_envelope_keys() -> None:
    ok_variant = _variants(record_schema())["ok"]
    properties = cast("Mapping[str, JSONValue]", ok_variant["properties"])
    envelope = cast("Mapping[str, JSONValue]", properties["envelope"])
    assert set(cast("list[str]", envelope["required"])) == set(_SAMPLE_ENVELOPE)
