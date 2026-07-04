from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner, Result

import mistral_cli.cli.ocr as ocr_cli
from mistral_cli.cli.main import cli
from mistral_cli.config import ConfigStore
from mistral_cli.errors import ConfigError
from mistral_cli.models import JSONMapping, JSONValue, OcrRequest
from mistral_cli.storage import ResultStore

SAVE_TIME = datetime(2025, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
SAVE_STAMP = "20250102T030405.678901Z"
DEFAULT_RESPONSE: dict[str, JSONValue] = {
    "model": "mistral-ocr-latest",
    "pages": [
        {
            "index": 0,
            "header": "Header",
            "markdown": "# Readable OCR\n\nHello from OCR.",
            "footer": "Footer",
        }
    ],
}


def default_failures() -> dict[str, BaseException]:
    return {}


def default_requests() -> list[OcrRequest]:
    return []


@dataclass
class FakeGateway:
    response: JSONMapping = field(default_factory=lambda: DEFAULT_RESPONSE)
    failures: dict[str, BaseException] = field(default_factory=default_failures)
    requests: list[OcrRequest] = field(default_factory=default_requests)

    def ocr(self, request: OcrRequest) -> JSONMapping:
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
            ["--config", str(self.config_path), "ocr", *arguments],
            env=invocation_env,
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    config_path = tmp_path / "config.toml"
    ConfigStore(config_path).set("api-key", "config-secret")
    output_root = tmp_path / "results"
    gateway = FakeGateway()
    api_keys: list[str] = []

    def gateway_factory(api_key: str) -> FakeGateway:
        api_keys.append(api_key)
        return gateway

    monkeypatch.setattr(ocr_cli, "create_gateway", gateway_factory)
    monkeypatch.setattr(
        ocr_cli,
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


def make_pdf(tmp_path: Path, name: str = "sample.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7\n" + name.encode())
    return path


def test_help_describes_sources_options_and_defaults() -> None:
    result = CliRunner().invoke(cli, ["ocr", "--help"])

    assert result.exit_code == 0
    assert "SOURCE..." in result.stdout
    for option in (
        "--model",
        "--pages",
        "--table-format",
        "--extract-header",
        "--extract-footer",
        "--include-images",
        "--image-limit",
        "--image-min-size",
        "--include-blocks",
        "--confidence",
        "--output-dir",
        "--format",
        "--timeout",
        "--stdout",
    ):
        assert option in result.stdout
    assert "mistral-ocr-latest" in result.stdout
    assert "inline" in result.stdout
    assert "both" in result.stdout
    assert "300" in result.stdout


def test_local_pdf_defaults_save_both_readable_markdown_and_json(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke(str(source))

    assert result.exit_code == 0, result.output
    assert len(harness.gateway.requests) == 1
    request = harness.gateway.requests[0]
    assert request.source.path == source
    assert request.model == "mistral-ocr-latest"
    assert request.pages is None
    assert request.table_format is None
    assert request.extract_header is False
    assert request.extract_footer is False
    assert request.include_images is False
    assert request.image_limit is None
    assert request.image_min_size is None
    assert request.include_blocks is False
    assert request.confidence is None
    assert request.timeout_ms == 300_000

    markdown_path = harness.output_root / "ocr" / f"{SAVE_STAMP}-sample.pdf.md"
    json_path = harness.output_root / "ocr" / f"{SAVE_STAMP}-sample.pdf.json"
    assert markdown_path.exists()
    assert json_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# OCR Result" in markdown
    assert "# Readable OCR" in markdown
    envelope = cast(dict[str, object], json.loads(json_path.read_text("utf-8")))
    assert envelope["schema_version"] == 1
    assert cast(dict[str, object], envelope["source"])["kind"] == "file"
    assert cast(dict[str, object], envelope["request"])["model"] == (
        "mistral-ocr-latest"
    )
    assert str(markdown_path) in result.stderr
    assert str(json_path) in result.stderr
    assert f"Processing: {source}" in result.stderr
    assert "1 succeeded, 0 failed" in result.stderr
    assert result.stdout == ""
    assert harness.api_keys == ["config-secret"]


@pytest.mark.parametrize(
    ("url", "expected_document"),
    [
        (
            "https://example.test/files/report.pdf?download=1",
            {"type": "document_url", "document_url": "unused"},
        ),
        (
            "https://example.test/files/scan.png",
            {"type": "image_url", "image_url": "unused"},
        ),
    ],
)
def test_url_source_kind_reaches_gateway_request(
    harness: Harness,
    url: str,
    expected_document: dict[str, str],
) -> None:
    result = harness.invoke(url)

    assert result.exit_code == 0
    request = harness.gateway.requests[0]
    assert request.source.value == url
    assert request.source.kind.value == "url"
    expected_type = expected_document["type"].removesuffix("_url")
    assert request.source.ocr_kind.value == expected_type


def test_every_ocr_api_option_maps_exactly(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke(
        str(source),
        "--model",
        "custom-ocr",
        "--pages",
        "0, 2-4",
        "--table-format",
        "html",
        "--extract-header",
        "--extract-footer",
        "--include-images",
        "--image-limit",
        "7",
        "--image-min-size",
        "128",
        "--include-blocks",
        "--confidence",
        "word",
        "--timeout",
        "1.2341",
    )

    assert result.exit_code == 0, result.output
    request = harness.gateway.requests[0]
    assert request.model == "custom-ocr"
    assert request.pages == "0,2-4"
    assert request.table_format == "html"
    assert request.extract_header is True
    assert request.extract_footer is True
    assert request.include_images is True
    assert request.image_limit == 7
    assert request.image_min_size == 128
    assert request.include_blocks is True
    assert request.confidence == "word"
    assert request.timeout_ms == 1235


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
    source = make_pdf(tmp_path)

    result = harness.invoke(str(source), *arguments)

    assert result.exit_code == 0
    files = set((harness.output_root / "ocr").iterdir())
    assert {path.suffix for path in files} == extensions


def test_custom_output_directory_is_used(harness: Harness, tmp_path: Path) -> None:
    source = make_pdf(tmp_path)
    output_dir = tmp_path / "custom-output"

    result = harness.invoke(str(source), "--output-dir", str(output_dir))

    assert result.exit_code == 0
    assert {path.suffix for path in output_dir.iterdir()} == {".md", ".json"}
    assert not (harness.output_root / "ocr").exists()


def test_stdout_is_exact_nonwrapping_markdown_and_still_persists(
    harness: Harness,
    tmp_path: Path,
) -> None:
    long_line = "x" * 200
    harness.gateway.response = {
        "pages": [{"index": 0, "markdown": long_line}],
    }
    source = make_pdf(tmp_path)

    result = harness.invoke(str(source), "--stdout", "--format", "md")

    saved = next((harness.output_root / "ocr").glob("*.md"))
    assert result.exit_code == 0
    assert result.stdout == saved.read_text(encoding="utf-8")
    assert long_line in result.stdout
    assert result.stderr


def test_multiple_stdout_documents_have_one_separator_between_successes(
    harness: Harness,
    tmp_path: Path,
) -> None:
    sources = [
        make_pdf(tmp_path, "first.pdf"),
        make_pdf(tmp_path, "second.pdf"),
        make_pdf(tmp_path, "third.pdf"),
    ]

    result = harness.invoke(*(str(source) for source in sources), "--stdout")

    assert result.exit_code == 0
    saved = sorted((harness.output_root / "ocr").glob("*.md"))
    expected = "\n\n---\n\n".join(path.read_text(encoding="utf-8") for path in saved)
    assert result.stdout == expected
    assert result.stdout.count("\n\n---\n\n") == 2
    assert not result.stdout.startswith("---")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--pages", "3-1"), "ascending"),
        (("--image-limit", "-1"), "nonnegative"),
        (("--image-min-size", "-1"), "nonnegative"),
        (("--image-limit", "1"), "require --include-images"),
        (("--image-min-size", "1"), "require --include-images"),
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
    source = make_pdf(tmp_path)

    result = harness.invoke(str(source), *arguments)

    assert result.exit_code != 0
    assert message in result.stderr
    assert harness.gateway.requests == []
    assert harness.api_keys == []


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("missing.pdf", "does not exist"),
        ("ftp://example.test/file.pdf", "unsupported"),
        ("https://", "valid authority"),
        ("https://example.test:bad/file.pdf", "invalid"),
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


@pytest.mark.parametrize(
    ("source_exists", "arguments", "message"),
    [
        (False, (), "does not exist"),
        (True, ("--pages", "4-2"), "ascending"),
    ],
)
def test_invalid_input_is_reported_before_missing_api_key_or_runtime_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_exists: bool,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    source = tmp_path / "source.pdf"
    if source_exists:
        source.write_bytes(b"%PDF-1.7\n")
    gateway_factory_called = False

    def gateway_factory(api_key: str) -> FakeGateway:
        nonlocal gateway_factory_called
        gateway_factory_called = True
        pytest.fail(f"unexpected runtime creation for {len(api_key)} byte key")

    monkeypatch.setattr(ocr_cli, "create_gateway", gateway_factory)

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(tmp_path / "missing-config.toml"),
            "ocr",
            str(source),
            *arguments,
        ],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 1
    assert message in result.stderr
    assert "No API key configured" not in result.stderr
    assert gateway_factory_called is False


@pytest.mark.parametrize("debug", [False, True])
def test_pre_runtime_invalid_source_redacts_environment_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,
) -> None:
    secret = "environment-invalid-source-secret"
    source = tmp_path / f"missing-{secret}.pdf"

    def fail_gateway_factory(api_key: str) -> FakeGateway:
        pytest.fail(f"unexpected runtime creation for {len(api_key)} byte key")

    monkeypatch.setattr(ocr_cli, "create_gateway", fail_gateway_factory)
    arguments = ["--debug"] if debug else []
    result = CliRunner().invoke(
        cli,
        [
            *arguments,
            "--config",
            str(tmp_path / "missing-config.toml"),
            "ocr",
            str(source),
        ],
        env={"MISTRAL_API_KEY": secret},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert secret not in result.stderr
    assert "[REDACTED]" in result.stderr
    assert ("Traceback" in result.stderr) is debug


@pytest.mark.parametrize("debug", [False, True])
def test_pre_runtime_invalid_source_redacts_configured_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,
) -> None:
    secret = "configured-invalid-source-secret"
    config_path = tmp_path / "config.toml"
    ConfigStore(config_path).set("api-key", secret)
    source = tmp_path / f"missing-{secret}.pdf"

    def fail_gateway_factory(api_key: str) -> FakeGateway:
        pytest.fail(f"unexpected runtime creation for {len(api_key)} byte key")

    monkeypatch.setattr(ocr_cli, "create_gateway", fail_gateway_factory)
    arguments = ["--debug"] if debug else []
    result = CliRunner().invoke(
        cli,
        [
            *arguments,
            "--config",
            str(config_path),
            "ocr",
            str(source),
        ],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert secret not in result.stderr
    assert "[REDACTED]" in result.stderr
    assert ("Traceback" in result.stderr) is debug


def test_redaction_precedes_terminal_sanitization_for_control_character_key(
    tmp_path: Path,
) -> None:
    secret = "abc\rdef"
    normalized_secret = "abcdef"
    source = tmp_path / f"missing-{secret}.pdf"

    result = CliRunner().invoke(
        cli,
        [
            "--debug",
            "--config",
            str(tmp_path / "missing-config.toml"),
            "ocr",
            str(source),
        ],
        env={"MISTRAL_API_KEY": secret},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert secret not in result.stderr
    assert normalized_secret not in result.stderr
    assert "[REDACTED]" in result.stderr


def test_malformed_config_does_not_mask_pre_runtime_input_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("api_key = [", encoding="utf-8")
    source = tmp_path / "missing.pdf"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "ocr", str(source)],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert "Could not parse configuration" not in result.stderr


def test_missing_api_key_is_clean_setup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path)
    called = False

    def gateway_factory(api_key: str) -> FakeGateway:
        nonlocal called
        called = True
        return FakeGateway()

    monkeypatch.setattr(ocr_cli, "create_gateway", gateway_factory)
    result = CliRunner().invoke(
        cli,
        ["--config", str(tmp_path / "missing.toml"), "ocr", str(source)],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 3
    assert "No API key configured" in result.stderr
    assert "mistral config set api-key" in result.stderr
    assert "Traceback" not in result.stderr
    assert called is False


def test_malformed_config_is_clean_setup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("api_key = [", encoding="utf-8")
    source = make_pdf(tmp_path)

    def fail_gateway_factory(api_key: str) -> FakeGateway:
        pytest.fail(f"gateway must not be constructed for {len(api_key)} byte key")

    monkeypatch.setattr(
        ocr_cli,
        "create_gateway",
        fail_gateway_factory,
    )

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "ocr", str(source)],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 3
    assert "Could not parse configuration" in result.stderr
    assert "Traceback" not in result.stderr


def test_environment_api_key_takes_precedence_without_leaking(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)
    secret = "environment-secret-value"

    result = harness.invoke(
        str(source),
        env={"MISTRAL_API_KEY": secret},
    )

    assert result.exit_code == 0
    assert harness.api_keys == [secret]
    assert secret not in result.stdout + result.stderr
    assert "config-secret" not in result.stdout + result.stderr


def test_api_key_is_redacted_if_it_appears_in_source_text(
    harness: Harness,
) -> None:
    secret = "source-query-secret"
    source = f"https://example.test/report.pdf?token={secret}"

    result = harness.invoke(source, env={"MISTRAL_API_KEY": secret})

    assert result.exit_code == 0
    assert secret not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stderr


def test_api_key_is_redacted_from_entire_translated_error_line(
    harness: Harness,
    tmp_path: Path,
) -> None:
    secret = "config-secret"
    source = make_pdf(tmp_path)
    harness.gateway.failures[str(source)] = ConfigError(
        f"upstream accidentally included {secret}"
    )

    result = harness.invoke(str(source))

    assert result.exit_code == 1
    assert secret not in result.stderr
    assert "upstream accidentally included [REDACTED]" in result.stderr


def test_api_key_is_recursively_redacted_from_all_persisted_and_stdout_data(
    harness: Harness,
) -> None:
    secret = "config-secret"
    source = f"https://example.test/{secret}.pdf?token={secret}"
    harness.gateway.response = {
        f"response-{secret}": {
            "nested": [
                secret,
                {"message": f"response leaked {secret}"},
            ]
        },
        "model": f"model-{secret}",
        "pages": [
            {
                "index": 0,
                "header": f"header {secret}",
                "markdown": f"body {secret}",
                "footer": f"footer {secret}",
            }
        ],
    }

    result = harness.invoke(source, "--stdout")

    assert result.exit_code == 0
    saved_files = list((harness.output_root / "ocr").iterdir())
    assert {path.suffix for path in saved_files} == {".md", ".json"}
    markdown_path = next(path for path in saved_files if path.suffix == ".md")
    json_path = next(path for path in saved_files if path.suffix == ".json")
    persisted_markdown = markdown_path.read_text(encoding="utf-8")
    persisted_json = json_path.read_text(encoding="utf-8")
    assert result.stdout == persisted_markdown
    assert secret not in result.stdout
    assert secret not in persisted_markdown
    assert secret not in persisted_json
    assert all(secret not in str(path) for path in saved_files)
    assert persisted_json.count("[REDACTED]") >= 6


def test_terminal_sanitized_markdown_is_identical_in_stdout_and_storage(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)
    harness.gateway.response = {
        "pages": [
            {
                "index": 0,
                "markdown": (
                    "before\x1b[31mred\x1b[0m\x1b]0;stolen-title\x07after\rreturn\x00"
                ),
            }
        ]
    }

    result = harness.invoke(str(source), "--stdout", "--format", "md")

    saved = next((harness.output_root / "ocr").glob("*.md"))
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
    source = make_pdf(tmp_path)
    harness.gateway.response = {
        "pages": [{"index": 0, "markdown": f"before {payload} after"}],
    }

    result = harness.invoke(
        str(source),
        "--stdout",
        "--format",
        "md",
        env={"MISTRAL_API_KEY": secret},
    )

    saved = next((harness.output_root / "ocr").glob("*.md"))
    persisted = saved.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert result.stdout == persisted
    assert secret not in result.stdout + result.stderr + persisted
    assert "[REDACTED]" in persisted


def test_failed_middle_source_continues_saves_successes_and_separates_stdout(
    harness: Harness,
    tmp_path: Path,
) -> None:
    first = make_pdf(tmp_path, "first.pdf")
    middle = make_pdf(tmp_path, "middle.pdf")
    third = make_pdf(tmp_path, "third.pdf")
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
    saved = sorted((harness.output_root / "ocr").glob("*.md"))
    assert [path.name.rsplit("-", maxsplit=1)[-1] for path in saved] == [
        "first.pdf.md",
        "third.pdf.md",
    ]
    expected = "\n\n---\n\n".join(path.read_text(encoding="utf-8") for path in saved)
    assert result.stdout == expected
    assert str(middle) in result.stderr
    assert "Unexpected failure" in result.stderr
    assert "2 succeeded, 1 failed" in result.stderr
    assert "private response body" not in result.stderr


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
    source = make_pdf(tmp_path)
    harness.gateway.failures[str(source)] = FakeSdkError(status_code, secret)

    result = harness.invoke(str(source))

    assert result.exit_code == 1
    assert message in result.stderr
    assert "private response body" not in result.stderr
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr


def test_debug_prints_diagnostics_to_stderr_with_api_key_redacted(
    harness: Harness,
    tmp_path: Path,
) -> None:
    api_key = "debug-secret-key"
    source = make_pdf(tmp_path)
    harness.gateway.failures[str(source)] = FakeSdkError(401, api_key)

    result = harness.runner.invoke(
        cli,
        [
            "--debug",
            "--config",
            str(harness.config_path),
            "ocr",
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


def test_untrusted_source_names_are_sanitized_in_terminal_output(
    harness: Harness,
) -> None:
    source = "https://example.test/%5Bbold%5Devil%5B%2Fbold%5D%1B%5B2J.pdf"

    result = harness.invoke(source)

    assert result.exit_code == 0
    assert "\x1b" not in result.stdout + result.stderr
    assert "[bold]evil" in result.stderr
    assert "[2J.pdf" in result.stderr
    assert result.stdout == ""


def test_keyboard_interrupt_aborts_and_does_not_process_later_sources(
    harness: Harness,
    tmp_path: Path,
) -> None:
    first = make_pdf(tmp_path, "first.pdf")
    later = make_pdf(tmp_path, "later.pdf")
    harness.gateway.failures[str(first)] = KeyboardInterrupt()

    result = harness.invoke(str(first), str(later))

    assert result.exit_code != 0
    assert "Aborted!" in result.stderr
    assert [request.source.path for request in harness.gateway.requests] == [first]
    assert not (harness.output_root / "ocr").exists()


def parse_ndjson(output: str) -> list[dict[str, JSONValue]]:
    lines = output.splitlines()
    assert all(line == line.encode("ascii", "strict").decode("ascii") for line in lines)
    return [cast("dict[str, JSONValue]", json.loads(line)) for line in lines]


def test_json_emits_one_record_per_source_and_summary(
    harness: Harness,
    tmp_path: Path,
) -> None:
    first = make_pdf(tmp_path, "first.pdf")
    second = make_pdf(tmp_path, "second.pdf")

    result = harness.invoke("--json", str(first), str(second))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["ok", "ok", "summary"]

    first_record = records[0]
    assert first_record["schema_version"] == 1
    assert first_record["source"] == str(first)
    envelope = cast("dict[str, JSONValue]", first_record["envelope"])
    assert envelope["schema_version"] == 1
    assert envelope["response"] == DEFAULT_RESPONSE
    saved = cast("dict[str, JSONValue]", first_record["saved"])
    assert Path(cast(str, saved["markdown"])).is_file()
    assert Path(cast(str, saved["json"])).is_file()

    assert records[-1] == {
        "schema_version": 1,
        "status": "summary",
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
    }


def test_json_reports_failures_in_band_and_exits_1(
    harness: Harness,
    tmp_path: Path,
) -> None:
    good = make_pdf(tmp_path, "good.pdf")

    result = harness.invoke("--json", str(tmp_path / "missing.pdf"), str(good))

    assert result.exit_code == 1
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["error", "ok", "summary"]
    error = cast("dict[str, JSONValue]", records[0]["error"])
    assert error["code"] == "input_error"
    assert isinstance(error["message"], str) and error["message"]
    assert error["status_code"] is None
    assert records[-1] == {
        "schema_version": 1,
        "status": "summary",
        "succeeded": 1,
        "failed": 1,
        "skipped": 0,
    }


def test_json_api_error_record_carries_status_code(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)
    harness.gateway.failures[str(source)] = FakeSdkError(429, "secret")

    result = harness.invoke("--json", str(source))

    assert result.exit_code == 1
    records = parse_ndjson(result.stdout)
    error = cast("dict[str, JSONValue]", records[0]["error"])
    assert error["code"] == "api_error"
    assert error["status_code"] == 429


def test_json_missing_api_key_emits_setup_error_record(
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["--config", str(tmp_path / "missing.toml"), "ocr", "--json", str(source)],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 3
    records = parse_ndjson(result.stdout)
    assert len(records) == 1
    assert records[0]["status"] == "error"
    assert records[0]["source"] is None
    error = cast("dict[str, JSONValue]", records[0]["error"])
    assert error["code"] == "config_error"


def test_json_and_stdout_are_mutually_exclusive(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke("--json", "--stdout", str(source))

    assert result.exit_code == 2
    assert "--json cannot be combined with --stdout." in result.stderr
    assert harness.gateway.requests == []


def test_quiet_suppresses_progress_but_not_errors(
    harness: Harness,
    tmp_path: Path,
) -> None:
    good = make_pdf(tmp_path, "good.pdf")

    result = harness.invoke("--quiet", str(tmp_path / "missing.pdf"), str(good))

    assert result.exit_code == 1
    assert "Processing:" not in result.stderr
    assert "Saved:" not in result.stderr
    assert "Summary:" not in result.stderr
    assert "missing.pdf" in result.stderr


def test_no_save_writes_no_files_and_reports_null_paths(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke("--no-save", "--json", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert records[0]["saved"] == {"markdown": None, "json": None}
    assert not harness.output_root.exists()


def test_no_save_requires_a_stdout_mode(harness: Harness, tmp_path: Path) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke("--no-save", str(source))

    assert result.exit_code == 2
    assert "--no-save requires --json or --stdout." in result.stderr


def test_no_save_conflicts_with_output_dir(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke(
        "--no-save", "--json", "--output-dir", str(tmp_path / "out"), str(source)
    )

    assert result.exit_code == 2
    assert "--no-save cannot be combined with --output-dir." in result.stderr


def test_dry_run_validates_without_api_key_or_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path)

    def fail_gateway_factory(api_key: str) -> FakeGateway:
        pytest.fail("gateway must not be constructed during --dry-run")

    monkeypatch.setattr(ocr_cli, "create_gateway", fail_gateway_factory)
    result = CliRunner().invoke(
        cli,
        ["--config", str(tmp_path / "missing.toml"), "ocr", "--dry-run", str(source)],
        env={"MISTRAL_API_KEY": ""},
    )

    assert result.exit_code == 0
    assert "Would process:" in result.stderr
    assert "mistral-ocr-latest" in result.stderr
    assert result.stdout == ""


def test_dry_run_json_emits_request_records(
    harness: Harness,
    tmp_path: Path,
) -> None:
    source = make_pdf(tmp_path)

    result = harness.invoke("--dry-run", "--json", "--pages", "0,2-4", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    assert [record["status"] for record in records] == ["dry_run", "summary"]
    request = cast("dict[str, JSONValue]", records[0]["request"])
    assert request["model"] == "mistral-ocr-latest"
    assert request["pages"] == "0,2-4"
    assert harness.gateway.requests == []
    assert harness.api_keys == []


def test_dry_run_reports_invalid_sources_and_exits_1(
    harness: Harness,
    tmp_path: Path,
) -> None:
    good = make_pdf(tmp_path)

    result = harness.invoke("--dry-run", str(tmp_path / "missing.pdf"), str(good))

    assert result.exit_code == 1
    assert "Would process:" in result.stderr
    assert harness.gateway.requests == []


def test_json_output_survives_control_character_injection(
    harness: Harness,
    tmp_path: Path,
) -> None:
    hostile = "\x1b]0;evil\x07 \x9b31m plain café"
    harness.gateway.response = {
        "model": "mistral-ocr-latest",
        "pages": [{"index": 0, "markdown": hostile}],
    }
    source = make_pdf(tmp_path)

    result = harness.invoke("--json", "--no-save", str(source))

    assert result.exit_code == 0
    records = parse_ndjson(result.stdout)
    envelope = cast("dict[str, JSONValue]", records[0]["envelope"])
    response = cast("dict[str, JSONValue]", envelope["response"])
    pages = cast("list[JSONValue]", response["pages"])
    page = cast("dict[str, JSONValue]", pages[0])
    assert page["markdown"] == hostile


def test_second_identical_invocation_skips_duplicate(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_pdf(tmp_path)

    first = harness.invoke(str(source))
    assert first.exit_code == 0

    second = harness.invoke(str(source))

    assert second.exit_code == 0
    assert len(harness.gateway.requests) == 1
    assert "Skipping duplicate:" in second.stderr
    assert "Existing:" in second.stderr
    assert "0 succeeded, 0 failed, 1 skipped" in second.stderr


def test_force_reprocesses_and_records_a_second_index_entry(
    harness: Harness, tmp_path: Path
) -> None:
    source = make_pdf(tmp_path)
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


def test_differently_named_sources_have_distinct_content_and_are_not_duplicates(
    harness: Harness, tmp_path: Path
) -> None:
    first = make_pdf(tmp_path, "first.pdf")
    second = make_pdf(tmp_path, "second.pdf")
    assert first.read_bytes() != second.read_bytes()

    harness.invoke(str(first))
    result = harness.invoke(str(second))

    assert result.exit_code == 0
    assert len(harness.gateway.requests) == 2
    assert "Skipping duplicate:" not in result.stderr
