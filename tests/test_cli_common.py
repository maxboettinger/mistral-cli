from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import click
import pytest

from mistral_cli.cli.common import (
    candidate_secrets,
    redact_result,
    resolve_api_key,
    safe_terminal_text,
)
from mistral_cli.config import ConfigStore
from mistral_cli.models import ApiResult, InputSource, Operation, SourceKind


def empty_strings() -> list[str]:
    return []


def empty_optional_strings() -> list[str | None]:
    return []


@dataclass
class RecordingConsoles:
    stderr: list[str] = field(default_factory=empty_strings)
    debug_contexts: list[str | None] = field(default_factory=empty_optional_strings)

    def write_stderr(self, payload: str) -> None:
        self.stderr.append(payload)

    def print_debug_exception(
        self,
        error: Exception,
        *,
        secrets: tuple[str, ...],
        context: str | None = None,
    ) -> None:
        assert error
        assert isinstance(secrets, tuple)
        self.debug_contexts.append(context)


@dataclass(frozen=True)
class FakeContext:
    config_path: Path
    debug: bool
    consoles: RecordingConsoles


def test_candidate_secrets_collects_environment_and_configured_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    ConfigStore(config_path).set("api-key", "configured-secret")
    monkeypatch.setenv("MISTRAL_API_KEY", "environment-secret")
    context = FakeContext(config_path, False, RecordingConsoles())

    assert candidate_secrets(context) == (
        "environment-secret",
        "configured-secret",
    )


def test_candidate_secrets_ignores_config_error_during_input_reporting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("api_key = [", encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "environment-secret")
    context = FakeContext(config_path, False, RecordingConsoles())

    assert candidate_secrets(context) == ("environment-secret",)


def test_safe_terminal_text_redacts_before_sanitizing_control_key() -> None:
    secret = "abc\rdef"

    result = safe_terminal_text(f"prefix {secret} suffix", (secret,))

    assert result == "prefix [REDACTED] suffix"
    assert "abcdef" not in result


def test_redact_result_recursively_cleans_source_keys_and_values() -> None:
    secret = "private-key"
    result = ApiResult(
        operation=Operation.TRANSCRIPTION,
        source=InputSource(
            kind=SourceKind.FILE,
            value=f"/audio/{secret}.mp3",
            filename=f"{secret}.mp3",
            path=Path(f"/audio/{secret}.mp3"),
        ),
        request_metadata={
            f"request-{secret}": [
                secret,
                {"nested": f"value-{secret}"},
            ],
        },
        response={
            secret: "first",
            f"{secret}{secret}": f"response-{secret}",
        },
        created_at=datetime(2025, 1, 2, tzinfo=UTC),
    )

    safe_result = redact_result(result, (secret,))

    assert secret not in repr(safe_result)
    assert safe_result.source.value == "/audio/[REDACTED].mp3"
    assert safe_result.source.path == Path("/audio/[REDACTED].mp3")
    assert safe_result.request_metadata == {
        "request-[REDACTED]": [
            "[REDACTED]",
            {"nested": "value-[REDACTED]"},
        ]
    }
    assert safe_result.response == {
        "[REDACTED]": "first",
        "[REDACTED][REDACTED]": "response-[REDACTED]",
    }


def test_resolve_api_key_reports_safe_setup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    consoles = RecordingConsoles()
    context = FakeContext(tmp_path / "missing.toml", False, consoles)

    with pytest.raises(click.exceptions.Exit) as caught:
        resolve_api_key(
            context,
            (),
            setup_debug_context="setting up test command",
        )

    assert caught.value.exit_code == 1
    assert "No API key configured" in "".join(consoles.stderr)
    assert consoles.debug_contexts == []
