from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner, Result

from mistral_cli.cli.main import cli
from mistral_cli.config import ConfigStore
from mistral_cli.errors import ConfigError
from mistral_cli.models import JSONMapping, JSONValue, TranscriptionRequest
from mistral_cli.storage import ResultStore

SAVE_TIME = datetime(2025, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
SAVE_STAMP = "20250102T030405.678901Z"
DEFAULT_RESPONSE: dict[str, JSONValue] = {
    "model": "voxtral-mini-latest",
    "text": "Hello from audio.",
}


def default_failures() -> dict[str, BaseException]:
    return {}


def default_requests() -> list[TranscriptionRequest]:
    return []


@dataclass
class FakeGateway:
    response: JSONMapping = field(default_factory=lambda: DEFAULT_RESPONSE)
    failures: dict[str, BaseException] = field(default_factory=default_failures)
    requests: list[TranscriptionRequest] = field(default_factory=default_requests)

    def transcribe(self, request: TranscriptionRequest) -> JSONMapping:
        self.requests.append(request)
        error = self.failures.get(request.source.value)
        if error is not None:
            raise error
        return self.response


class FakeSdkError(Exception):
    def __init__(self, status_code: int | None, secret: str) -> None:
        super().__init__(f"private response body containing {secret}")
        self.status_code = status_code
        self.message = f"private SDK message containing {secret}"
        self.body = {"detail": f"private body containing {secret}"}


@dataclass(frozen=True)
class Harness:
    runner: CliRunner
    config_path: Path
    output_root: Path
    gateway: FakeGateway
    api_keys: list[str]

    def invoke(
        self,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> Result:
        invocation_env = {"MISTRAL_API_KEY": ""}
        if env is not None:
            invocation_env.update(env)
        return self.runner.invoke(
            cli,
            ["--config", str(self.config_path), "transcribe", *arguments],
            env=invocation_env,
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    import mistral_cli.cli.transcribe as transcribe_cli

    config_path = tmp_path / "config.toml"
    ConfigStore(config_path).set("api-key", "config-secret")
    output_root = tmp_path / "results"
    gateway = FakeGateway()
    api_keys: list[str] = []

    def gateway_factory(api_key: str) -> FakeGateway:
        api_keys.append(api_key)
        return gateway

    monkeypatch.setattr(transcribe_cli, "create_gateway", gateway_factory)
    monkeypatch.setattr(
        transcribe_cli,
        "create_result_store",
        lambda: ResultStore(
            base_dir=output_root,
            clock=lambda: SAVE_TIME,
        ),
    )
    return Harness(
        runner=CliRunner(),
        config_path=config_path,
        output_root=output_root,
        gateway=gateway,
        api_keys=api_keys,
    )


def make_audio(tmp_path: Path, name: str = "sample.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(b"ID3\x04\x00\x00" + name.encode())
    return path


def test_help_describes_sources_options_and_defaults() -> None:
    result = CliRunner().invoke(cli, ["transcribe", "--help"])

    assert result.exit_code == 0
    assert "SOURCE..." in result.stdout
    for option in (
        "--model",
        "--language",
        "--temperature",
        "--diarize",
        "--context-bias",
        "--timestamps",
        "--output-dir",
        "--format",
        "--timeout",
        "--stdout",
    ):
        assert option in result.stdout
    assert "voxtral-" in result.stdout
    assert "mini-latest" in result.stdout
    assert "both" in result.stdout
    assert "300" in result.stdout


def test_local_audio_defaults_save_both_markdown_and_full_json(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(str(source))

    assert result.exit_code == 0, result.output
    assert len(harness.gateway.requests) == 1
    request = harness.gateway.requests[0]
    assert request.source.path == source
    assert request.source.filename == "sample.mp3"
    assert request.model == "voxtral-mini-latest"
    assert request.language is None
    assert request.temperature is None
    assert request.diarize is False
    assert request.context_bias == ()
    assert request.timestamps == ()
    assert request.timeout_ms == 300_000

    markdown_path = (
        harness.output_root / "transcriptions" / f"{SAVE_STAMP}-sample.mp3.md"
    )
    json_path = harness.output_root / "transcriptions" / f"{SAVE_STAMP}-sample.mp3.json"
    markdown = markdown_path.read_text(encoding="utf-8")
    envelope = cast(dict[str, object], json.loads(json_path.read_text("utf-8")))
    assert "# Transcription Result" in markdown
    assert "Hello from audio." in markdown
    assert envelope["schema_version"] == 1
    assert cast(dict[str, object], envelope["source"])["kind"] == "file"
    assert cast(dict[str, object], envelope["request"]) == {
        "model": "voxtral-mini-latest",
        "language": None,
        "temperature": None,
        "diarize": False,
        "context_bias": [],
        "timestamps": [],
        "timeout_ms": 300000,
    }
    assert cast(dict[str, object], envelope["response"]) == DEFAULT_RESPONSE
    assert str(markdown_path) in result.stderr
    assert str(json_path) in result.stderr
    assert f"Processing: {source}" in result.stderr
    assert "1 succeeded, 0 failed" in result.stderr
    assert result.stdout == ""
    assert harness.api_keys == ["config-secret"]


def test_url_audio_reaches_gateway(harness: Harness) -> None:
    url = "https://example.test/files/interview.mp3?download=1"

    result = harness.invoke(url)

    assert result.exit_code == 0
    request = harness.gateway.requests[0]
    assert request.source.value == url
    assert request.source.path is None
    assert request.source.kind.value == "url"
    assert request.source.filename == "interview.mp3"


def test_every_transcription_api_option_maps_exactly(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(
        str(source),
        "--model",
        "custom-voxtral",
        "--temperature",
        "0",
        "--diarize",
        "--context-bias",
        "Mistral",
        "--context-bias",
        "CLI",
        "--timestamps",
        "segment",
        "--timestamps",
        "word",
        "--timeout",
        "1.2341",
    )

    assert result.exit_code == 0, result.output
    request = harness.gateway.requests[0]
    assert request.model == "custom-voxtral"
    assert request.language is None
    assert request.temperature == 0
    assert request.diarize is True
    assert request.context_bias == ("Mistral", "CLI")
    assert request.timestamps == ("segment", "word")
    assert request.timeout_ms == 1235


def test_language_maps_exactly(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(str(source), "--language", "de")

    assert result.exit_code == 0
    assert harness.gateway.requests[0].language == "de"


def test_timestamps_choice_is_case_insensitive(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(str(source), "--timestamps", "SEGMENT")

    assert result.exit_code == 0, result.output
    assert harness.gateway.requests[0].timestamps == ("segment",)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--language", "de", "--timestamps", "segment"), "cannot be combined"),
        (("--context-bias", " "), "must not be blank"),
        (("--temperature", "nan"), "finite"),
        (("--temperature", "inf"), "finite"),
        (("--timeout", "0"), "greater than zero"),
        (("--timeout", "nan"), "finite"),
        (("--timeout", "inf"), "finite"),
    ],
)
def test_invalid_options_fail_without_gateway_call(
    harness: Harness,
    tmp_path: Path,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(str(source), *arguments)

    assert result.exit_code != 0
    assert message in result.stderr
    assert harness.gateway.requests == []
    assert harness.api_keys == []


def test_more_than_100_context_bias_values_fail_without_gateway(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)
    arguments = tuple(
        argument
        for number in range(101)
        for argument in ("--context-bias", f"term-{number}")
    )

    result = harness.invoke(str(source), *arguments)

    assert result.exit_code == 1
    assert "at most 100" in result.stderr
    assert harness.gateway.requests == []
    assert harness.api_keys == []


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("missing.mp3", "does not exist"),
        ("ftp://example.test/file.mp3", "unsupported"),
        ("https://", "valid authority"),
        ("https://example.test:bad/file.mp3", "invalid"),
    ],
)
def test_invalid_source_fails_without_gateway_call(
    harness: Harness,
    source: str,
    message: str,
) -> None:
    result = harness.invoke(source)

    assert result.exit_code == 1
    assert message in result.stderr
    assert harness.gateway.requests == []
    assert harness.api_keys == []
    assert "0 succeeded, 1 failed" in result.stderr


def test_invalid_source_precedes_missing_key_and_malformed_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mistral_cli.cli.transcribe as transcribe_cli

    config_path = tmp_path / "bad.toml"
    config_path.write_text("api_key = [", encoding="utf-8")
    called = False

    def gateway_factory(api_key: str) -> FakeGateway:
        nonlocal called
        called = True
        return FakeGateway()

    monkeypatch.setattr(transcribe_cli, "create_gateway", gateway_factory)
    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "transcribe",
            str(tmp_path / "missing.mp3"),
        ],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert "Could not parse configuration" not in result.stderr
    assert called is False


@pytest.mark.parametrize(
    ("arguments", "extensions"),
    [
        (("--format", "md"), {".md"}),
        (("--format", "json"), {".json"}),
        (("--format", "both"), {".md", ".json"}),
    ],
)
def test_format_selects_persisted_files(
    harness: Harness,
    tmp_path: Path,
    arguments: tuple[str, ...],
    extensions: set[str],
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke(str(source), *arguments)

    assert result.exit_code == 0
    files = set((harness.output_root / "transcriptions").iterdir())
    assert {path.suffix for path in files} == extensions


def test_custom_output_directory_is_used(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)
    output_dir = tmp_path / "custom-output"

    result = harness.invoke(str(source), "--output-dir", str(output_dir))

    assert result.exit_code == 0
    assert {path.suffix for path in output_dir.iterdir()} == {".md", ".json"}
    assert not (harness.output_root / "transcriptions").exists()


def test_stdout_is_exact_saved_sanitized_markdown_and_still_persists(
    harness: Harness,
    tmp_path: Path,
) -> None:
    harness.gateway.response = {
        "text": "before\x1b[31mred\x1b[0m\x1b]0;title\x07after\rreturn\x00"
    }
    source = make_audio(tmp_path)

    result = harness.invoke(str(source), "--stdout", "--format", "md")

    saved = next((harness.output_root / "transcriptions").glob("*.md"))
    persisted = saved.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert result.stdout == persisted
    assert "\x1b" not in persisted
    assert "\r" not in persisted
    assert "\x00" not in persisted
    assert "beforeredafterreturn" in persisted


def test_terminal_controls_cannot_split_secret_in_persisted_markdown_or_stdout(
    harness: Harness,
    tmp_path: Path,
) -> None:
    secret = "mistral-secret-key"
    payload = "mistral-\x1b[31msecret-key"
    source = make_audio(tmp_path)
    harness.gateway.response = {"text": f"before {payload} after"}

    result = harness.invoke(
        str(source),
        "--stdout",
        "--format",
        "md",
        env={"MISTRAL_API_KEY": secret},
    )

    saved = next((harness.output_root / "transcriptions").glob("*.md"))
    persisted = saved.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert result.stdout == persisted
    assert secret not in result.stdout + result.stderr + persisted
    assert "[REDACTED]" in persisted


def test_multiple_stdout_documents_have_one_separator_between_successes(
    harness: Harness,
    tmp_path: Path,
) -> None:
    sources = [
        make_audio(tmp_path, "first.mp3"),
        make_audio(tmp_path, "second.mp3"),
        make_audio(tmp_path, "third.mp3"),
    ]

    result = harness.invoke(*(str(source) for source in sources), "--stdout")

    assert result.exit_code == 0
    saved = sorted((harness.output_root / "transcriptions").glob("*.md"))
    expected = "\n\n---\n\n".join(path.read_text(encoding="utf-8") for path in saved)
    assert result.stdout == expected
    assert result.stdout.count("\n\n---\n\n") == 2
    assert not result.stdout.startswith("---")


def test_diarized_timestamp_segments_render_readable_markdown(
    harness: Harness,
    tmp_path: Path,
) -> None:
    harness.gateway.response = {
        "text": "fallback",
        "segments": [
            {"start": 0.0, "end": 1.25, "speaker": "SPEAKER_00", "text": "Hello."},
            {
                "start_time": 1.25,
                "end_time": 2.5,
                "speaker_id": "SPEAKER_01",
                "text": "Hi.",
            },
        ],
    }
    source = make_audio(tmp_path)

    result = harness.invoke(
        str(source),
        "--diarize",
        "--timestamps",
        "segment",
        "--stdout",
        "--format",
        "md",
    )

    assert result.exit_code == 0
    separator = "\N{EN DASH}"
    assert (
        f"**[00:00:00.000{separator}00:00:01.250] SPEAKER_00:** Hello." in result.stdout
    )
    assert f"**[00:00:01.250{separator}00:00:02.500] SPEAKER_01:** Hi." in result.stdout
    assert "\nfallback\n" not in result.stdout


def test_failed_middle_source_continues_saves_successes_and_separates_stdout(
    harness: Harness,
    tmp_path: Path,
) -> None:
    first = make_audio(tmp_path, "first.mp3")
    middle = make_audio(tmp_path, "middle.mp3")
    third = make_audio(tmp_path, "third.mp3")
    harness.gateway.failures[str(middle)] = RuntimeError("private response body")

    result = harness.invoke(
        str(first),
        str(middle),
        str(third),
        "--stdout",
        "--format",
        "md",
    )

    assert result.exit_code == 1
    assert [request.source.path for request in harness.gateway.requests] == [
        first,
        middle,
        third,
    ]
    saved = sorted((harness.output_root / "transcriptions").glob("*.md"))
    assert [path.name.rsplit("-", maxsplit=1)[-1] for path in saved] == [
        "first.mp3.md",
        "third.mp3.md",
    ]
    expected = "\n\n---\n\n".join(path.read_text(encoding="utf-8") for path in saved)
    assert result.stdout == expected
    assert str(middle) in result.stderr
    assert "Unexpected failure" in result.stderr
    assert "2 succeeded, 1 failed" in result.stderr
    assert "private response body" not in result.stderr


@pytest.mark.parametrize("debug", [False, True])
def test_pre_runtime_invalid_source_redacts_control_containing_environment_key(
    tmp_path: Path,
    debug: bool,
) -> None:
    secret = "abc\rdef"
    source = tmp_path / f"missing-{secret}.mp3"
    arguments = ["--debug"] if debug else []

    result = CliRunner().invoke(
        cli,
        [
            *arguments,
            "--config",
            str(tmp_path / "missing-config.toml"),
            "transcribe",
            str(source),
        ],
        env={"MISTRAL_API_KEY": secret},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert secret not in result.stderr
    assert "abcdef" not in result.stderr
    assert "[REDACTED]" in result.stderr
    assert ("Traceback" in result.stderr) is debug


def test_environment_api_key_takes_precedence_without_leaking(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)
    secret = "environment-secret-value"

    result = harness.invoke(str(source), env={"MISTRAL_API_KEY": secret})

    assert result.exit_code == 0
    assert harness.api_keys == [secret]
    assert secret not in result.stdout + result.stderr
    assert "config-secret" not in result.stdout + result.stderr


def test_api_key_is_recursively_redacted_from_persistence_and_stdout(
    harness: Harness,
) -> None:
    secret = "config-secret"
    source = f"https://example.test/{secret}.mp3?token={secret}"
    harness.gateway.response = {
        f"response-{secret}": {"nested": [secret, {"message": f"leak {secret}"}]},
        "model": f"model-{secret}",
        "text": f"body {secret}",
    }

    result = harness.invoke(source, "--stdout")

    assert result.exit_code == 0
    saved_files = list((harness.output_root / "transcriptions").iterdir())
    markdown_path = next(path for path in saved_files if path.suffix == ".md")
    json_path = next(path for path in saved_files if path.suffix == ".json")
    persisted_markdown = markdown_path.read_text(encoding="utf-8")
    persisted_json = json_path.read_text(encoding="utf-8")
    assert result.stdout == persisted_markdown
    assert secret not in result.stdout + result.stderr
    assert secret not in persisted_markdown
    assert secret not in persisted_json
    assert all(secret not in str(path) for path in saved_files)
    assert persisted_json.count("[REDACTED]") >= 5


def test_config_error_message_is_redacted(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_audio(tmp_path)
    harness.gateway.failures[str(source)] = ConfigError(
        "upstream accidentally included config-secret"
    )

    result = harness.invoke(str(source))

    assert result.exit_code == 1
    assert "config-secret" not in result.stderr
    assert "upstream accidentally included [REDACTED]" in result.stderr


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "Authentication failed"),
        (429, "rate limit"),
        (None, "Unexpected failure"),
    ],
)
def test_sdk_errors_are_safe_without_body_or_traceback(
    harness: Harness,
    tmp_path: Path,
    status_code: int | None,
    message: str,
) -> None:
    secret = "sdk-secret"
    source = make_audio(tmp_path)
    harness.gateway.failures[str(source)] = FakeSdkError(status_code, secret)

    result = harness.invoke(str(source))

    assert result.exit_code == 1
    assert message in result.stderr
    assert "private response body" not in result.stderr
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr


def test_debug_traceback_is_redacted_and_stays_on_stderr(
    harness: Harness,
    tmp_path: Path,
) -> None:
    api_key = "debug-secret-key"
    source = make_audio(tmp_path)
    harness.gateway.failures[str(source)] = FakeSdkError(401, api_key)

    result = harness.runner.invoke(
        cli,
        [
            "--debug",
            "--config",
            str(harness.config_path),
            "transcribe",
            str(source),
        ],
        env={"MISTRAL_API_KEY": api_key},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Traceback (most recent call last)" in result.stderr
    assert "FakeSdkError" in result.stderr
    assert str(source) in result.stderr
    assert "[REDACTED]" in result.stderr
    assert api_key not in result.stderr


def test_keyboard_interrupt_aborts_and_does_not_process_later_sources(
    harness: Harness,
    tmp_path: Path,
) -> None:
    first = make_audio(tmp_path, "first.mp3")
    later = make_audio(tmp_path, "later.mp3")
    harness.gateway.failures[str(first)] = KeyboardInterrupt()

    result = harness.invoke(str(first), str(later))

    assert result.exit_code != 0
    assert "Aborted!" in result.stderr
    assert [request.source.path for request in harness.gateway.requests] == [first]
    assert not (harness.output_root / "transcriptions").exists()


def parse_ndjson(output: str) -> list[dict[str, JSONValue]]:
    return [
        cast("dict[str, JSONValue]", json.loads(line)) for line in output.splitlines()
    ]


def test_json_emits_records_and_summary(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke("--json", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["ok", "summary"]
    envelope = cast("dict[str, JSONValue]", records[0]["envelope"])
    assert envelope["schema_version"] == 1
    assert envelope["response"] == DEFAULT_RESPONSE
    saved = cast("dict[str, JSONValue]", records[0]["saved"])
    assert Path(cast(str, saved["json"])).is_file()


def test_dry_run_json_needs_no_api_key(tmp_path: Path) -> None:
    source = make_audio(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "transcribe",
            "--dry-run",
            "--json",
            "--diarize",
            str(source),
        ],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["dry_run", "summary"]
    request = cast("dict[str, JSONValue]", records[0]["request"])
    assert request["model"] == "voxtral-mini-latest"
    assert request["diarize"] is True


def test_quiet_suppresses_progress_lines(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke("--quiet", str(source))

    assert result.exit_code == 0
    assert result.stderr == ""


def test_second_identical_invocation_skips_duplicate(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)

    first = harness.invoke(str(source))
    assert first.exit_code == 0

    second = harness.invoke(str(source))

    assert second.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert "Skipping duplicate:" in second.stderr
    assert "Existing:" in second.stderr
    assert "0 succeeded, 0 failed, 1 skipped" in second.stderr


def test_duplicate_skip_never_resolves_api_key(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    first = harness.invoke(str(source))
    assert first.exit_code == 0
    assert harness.api_keys == ["config-secret"]
    ConfigStore(harness.config_path).unset("api-key")

    second = harness.invoke(str(source))

    assert second.exit_code == 0
    assert harness.api_keys == ["config-secret"]
    assert len(harness.gateway.requests) == 1


def test_force_reprocesses_and_records_a_second_index_entry(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))

    result = harness.invoke("--force", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    index_lines = (
        (harness.output_root / "index.ndjson")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert len(index_lines) == 2


def test_same_filename_different_bytes_is_not_a_duplicate(
    harness: Harness, tmp_path: Path
) -> None:
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "clip.mp3"
    first.write_bytes(b"ID3\x04\x00\x00one")
    second = second_dir / "clip.mp3"
    second.write_bytes(b"ID3\x04\x00\x00two")
    harness.invoke(str(first))

    result = harness.invoke(str(second))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr


def test_different_request_options_is_not_a_duplicate(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))

    result = harness.invoke("--diarize", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr


def test_stdout_skip_reemits_the_saved_markdown_exactly(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    first = harness.invoke(str(source), "--stdout")
    assert first.exit_code == 0
    saved_markdown = next((harness.output_root / "transcriptions").glob("*.md"))
    saved_content = saved_markdown.read_text(encoding="utf-8")

    second = harness.invoke(str(source), "--stdout")

    assert second.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert second.stdout == saved_content


def test_json_skip_record_shape_and_summary_skipped_count(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    first = harness.invoke("--json", str(source))
    first_saved = cast("dict[str, JSONValue]", parse_ndjson(first.stdout)[0]["saved"])

    result = harness.invoke("--json", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["skipped", "summary"]
    skipped = records[0]
    assert skipped["source"] == str(source)
    assert skipped["reason"] == "duplicate"
    existing = cast("dict[str, JSONValue]", skipped["existing"])
    assert existing["markdown"] == first_saved["markdown"]
    assert existing["json"] == first_saved["json"]
    assert existing["model"] == "voxtral-mini-latest"
    assert records[-1] == {
        "schema_version": 1,
        "status": "summary",
        "succeeded": 0,
        "failed": 0,
        "skipped": 1,
    }


def test_dedupe_window_expiry_reprocesses_a_stale_entry(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))
    index_path = harness.output_root / "index.ndjson"
    entry = json.loads(index_path.read_text(encoding="utf-8").strip())
    stale = datetime.now(UTC) - timedelta(days=40)
    entry["saved_at"] = stale.isoformat().replace("+00:00", "Z")
    index_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = harness.invoke(str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr


def test_dedupe_window_override_treats_a_stale_entry_as_recent(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))
    index_path = harness.output_root / "index.ndjson"
    entry = json.loads(index_path.read_text(encoding="utf-8").strip())
    stale = datetime.now(UTC) - timedelta(days=40)
    entry["saved_at"] = stale.isoformat().replace("+00:00", "Z")
    index_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = harness.invoke("--dedupe-window", "3650", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert "Skipping duplicate:" in result.stderr


def test_dedupe_window_huge_value_skips_instead_of_raising_overflow_error(
    harness: Harness, tmp_path: Path
) -> None:
    """`--dedupe-window 1e9` exceeds timedelta's day limit; DedupeIndex.lookup
    must clamp it instead of letting OverflowError blow up the run."""
    source = make_audio(tmp_path)
    harness.invoke(str(source))

    result = harness.invoke("--dedupe-window", "1e9", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert "Skipping duplicate:" in result.stderr


def test_dedupe_window_tiny_value_forces_reprocessing(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))
    index_path = harness.output_root / "index.ndjson"
    entry = json.loads(index_path.read_text(encoding="utf-8").strip())
    recent_past = datetime.now(UTC) - timedelta(hours=1)
    entry["saved_at"] = recent_past.isoformat().replace("+00:00", "Z")
    index_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = harness.invoke("--dedupe-window", "0.000001", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr


@pytest.mark.parametrize("window", ["0", "-1", "inf"])
def test_dedupe_window_rejects_non_positive_or_infinite_values(
    harness: Harness, tmp_path: Path, window: str
) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke("--dedupe-window", window, str(source))

    assert result.exit_code == 2
    assert harness.gateway.requests == []


def test_dry_run_reports_would_skip_duplicate_and_still_counts_as_succeeded(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source))

    result = harness.invoke("--dry-run", str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert "Would skip (duplicate):" in result.stderr
    assert "1 succeeded, 0 failed, 0 skipped" in result.stderr


def test_dry_run_json_duplicate_record_carries_the_duplicate_object(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    first = harness.invoke("--json", str(source))
    first_saved = cast("dict[str, JSONValue]", parse_ndjson(first.stdout)[0]["saved"])

    result = harness.invoke("--dry-run", "--json", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["dry_run", "summary"]
    duplicate = cast("dict[str, JSONValue]", records[0]["duplicate"])
    assert duplicate["markdown"] == first_saved["markdown"]
    assert duplicate["json"] == first_saved["json"]
    assert records[-1]["succeeded"] == 1
    assert records[-1]["skipped"] == 0


def test_deleted_artifact_forces_reprocessing(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)
    harness.invoke(str(source), "--format", "md")
    saved_markdown = next((harness.output_root / "transcriptions").glob("*.md"))
    saved_markdown.unlink()

    result = harness.invoke(str(source), "--format", "md")

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr


def test_corrupt_index_line_does_not_block_processing(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_audio(tmp_path)
    index_path = harness.output_root / "index.ndjson"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("not-json-at-all\n", encoding="utf-8")

    result = harness.invoke(str(source))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 1
    lines = index_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_no_save_records_nothing_in_the_index(harness: Harness, tmp_path: Path) -> None:
    source = make_audio(tmp_path)

    result = harness.invoke("--no-save", "--json", str(source))

    assert result.exit_code == 0
    assert not (harness.output_root / "index.ndjson").exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX file permissions")
def test_unreadable_stored_markdown_falls_through_to_processing(
    harness: Harness, tmp_path: Path
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores file permission bits")
    source = make_audio(tmp_path)
    harness.invoke(str(source), "--stdout")
    saved_markdown = next((harness.output_root / "transcriptions").glob("*.md"))
    saved_markdown.chmod(0o000)

    try:
        result = harness.invoke(str(source), "--stdout")
    finally:
        saved_markdown.chmod(0o600)

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr
