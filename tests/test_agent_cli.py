from __future__ import annotations

import json
from typing import cast

from click.testing import CliRunner

from moxtral.cli.main import cli
from moxtral.models import JSONValue


def test_agent_prints_usage_guide() -> None:
    result = CliRunner().invoke(cli, ["agent"])

    assert result.exit_code == 0
    for expected in (
        "--json",
        "schema_version",
        "Exit codes",
        "--dry-run",
        "MISTRAL_API_KEY",
    ):
        assert expected in result.stdout


def test_agent_schema_prints_record_json_schema() -> None:
    result = CliRunner().invoke(cli, ["agent", "--schema"])

    assert result.exit_code == 0
    schema = cast("dict[str, JSONValue]", json.loads(result.stdout))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert isinstance(schema["oneOf"], list)


def test_root_help_mentions_agent_docs() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "moxtral agent" in result.stdout
