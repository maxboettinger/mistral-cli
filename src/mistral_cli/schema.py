from __future__ import annotations

from mistral_cli.models import JSONValue


def _nullable_string() -> dict[str, JSONValue]:
    return {"type": ["string", "null"]}


def record_schema() -> dict[str, JSONValue]:
    """Return the JSON Schema for one NDJSON stdout record."""
    schema_version: dict[str, JSONValue] = {"const": 1}
    envelope: dict[str, JSONValue] = {
        "type": "object",
        "required": [
            "schema_version",
            "created_at",
            "source",
            "request",
            "response",
            "cli_version",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "created_at": {"type": "string", "format": "date-time"},
            "source": {
                "type": "object",
                "required": ["kind", "value", "filename"],
                "properties": {
                    "kind": {"enum": ["file", "url"]},
                    "value": {"type": "string"},
                    "filename": {"type": "string"},
                },
            },
            "request": {"type": "object"},
            "response": {"type": "object"},
            "cli_version": {"type": "string"},
        },
    }
    ok_variant: dict[str, JSONValue] = {
        "required": ["schema_version", "status", "source", "envelope", "saved"],
        "properties": {
            "schema_version": schema_version,
            "status": {"const": "ok"},
            "source": {"type": "string"},
            "envelope": envelope,
            "saved": {
                "type": "object",
                "required": ["markdown", "json"],
                "properties": {
                    "markdown": _nullable_string(),
                    "json": _nullable_string(),
                },
            },
        },
    }
    error_variant: dict[str, JSONValue] = {
        "required": ["schema_version", "status", "source", "error"],
        "properties": {
            "schema_version": schema_version,
            "status": {"const": "error"},
            "source": _nullable_string(),
            "error": {
                "type": "object",
                "required": ["code", "message", "status_code"],
                "properties": {
                    "code": {
                        "enum": [
                            "input_error",
                            "config_error",
                            "api_error",
                            "persistence_error",
                            "unexpected_error",
                        ]
                    },
                    "message": {"type": "string"},
                    "status_code": {"type": ["integer", "null"]},
                },
            },
        },
    }
    dry_run_variant: dict[str, JSONValue] = {
        "required": ["schema_version", "status", "source", "request"],
        "properties": {
            "schema_version": schema_version,
            "status": {"const": "dry_run"},
            "source": {"type": "string"},
            "request": {"type": "object"},
        },
    }
    summary_variant: dict[str, JSONValue] = {
        "required": ["schema_version", "status", "succeeded", "failed"],
        "properties": {
            "schema_version": schema_version,
            "status": {"const": "summary"},
            "succeeded": {"type": "integer", "minimum": 0},
            "failed": {"type": "integer", "minimum": 0},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "mistral-cli --json output record",
        "description": (
            "One NDJSON record per line on stdout: one record per source, "
            "plus a final summary record."
        ),
        "type": "object",
        "oneOf": [ok_variant, error_variant, dry_run_variant, summary_variant],
    }
