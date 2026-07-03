from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from mistral_cli import errors as errors_module
from mistral_cli.errors import (
    ApiError,
    ConfigError,
    MistralCliError,
    format_debug_exception,
    redact,
    translate_exception,
)


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeSdkError(Exception):
    def __init__(
        self,
        *,
        status_code: object | None = None,
        raw_status_code: int | None = None,
        message: str = "untrusted response body",
        body: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.headers = {"x-request-id": "request-id"}
        self.body = body
        self.raw_response = (
            FakeResponse(raw_status_code) if raw_status_code is not None else None
        )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "Authentication failed. Check your Mistral API key and configuration."),
        (
            403,
            "Permission denied. Check your Mistral API key and account permissions.",
        ),
        (429, "Mistral rate limit reached. Wait and retry the request."),
        (422, "Invalid API request. Check the supplied options and try again."),
        (404, "Mistral rejected the request (HTTP 404)."),
        (503, "Mistral server error (HTTP 503). Wait and retry the request."),
    ],
)
def test_translate_exception_maps_api_statuses(
    status_code: int,
    expected: str,
) -> None:
    translated = translate_exception(
        FakeSdkError(status_code=status_code, message="private response body")
    )

    assert isinstance(translated, ApiError)
    assert translated.status_code == status_code
    assert str(translated) == expected
    assert "private response body" not in str(translated)


def test_translate_exception_reads_raw_response_status() -> None:
    translated = translate_exception(
        FakeSdkError(status_code="not-a-status", raw_status_code=401)
    )

    assert isinstance(translated, ApiError)
    assert translated.status_code == 401
    assert "API key" in str(translated)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("secret body"), "The Mistral request timed out. Try again."),
        (
            httpx.ReadTimeout("secret body"),
            "The Mistral request timed out. Try again.",
        ),
        (
            ConnectionError("secret body"),
            "Could not connect to Mistral. Check your network and try again.",
        ),
        (
            httpx.ConnectError("secret body"),
            "Could not connect to Mistral. Check your network and try again.",
        ),
    ],
)
def test_translate_exception_maps_transport_failures(
    error: Exception,
    expected: str,
) -> None:
    translated = translate_exception(error)

    assert isinstance(translated, ApiError)
    assert str(translated) == expected
    assert "secret body" not in str(translated)


def test_translate_exception_preserves_domain_error_identity() -> None:
    error = ConfigError("fix the config")

    assert translate_exception(error) is error


def test_translate_exception_hides_unknown_exception_details() -> None:
    translated = translate_exception(RuntimeError("response body with credentials"))

    assert type(translated) is MistralCliError
    assert str(translated) == "Unexpected failure. Run again with --debug for details."
    assert "credentials" not in str(translated)


def test_translate_exception_tolerates_hostile_public_attributes() -> None:
    class HostileError(Exception):
        @property
        def status_code(self) -> object:
            raise RuntimeError("property failed")

        @property
        def raw_response(self) -> object:
            raise RuntimeError("property failed")

    translated = translate_exception(HostileError("private response body"))

    assert type(translated) is MistralCliError
    assert "private response body" not in str(translated)


def test_redact_replaces_overlaps_repeats_blanks_and_unicode() -> None:
    text = "token-long token-long token blank 🔑秘密 and token"

    assert redact(
        text,
        ["token", "token-long", "", "   ", "🔑秘密"],
    ) == ("[REDACTED] [REDACTED] [REDACTED] blank [REDACTED] and [REDACTED]")


def _raised_error(api_key: str) -> Iterator[Exception]:
    try:
        local_context = f"key={api_key}"
        raise RuntimeError(f"request body contained {api_key}: {local_context}")
    except RuntimeError as error:
        yield error


def test_debug_exception_has_traceback_context_and_redacts_secrets() -> None:
    api_key = "mistral-secret-🔑"
    error = next(_raised_error(api_key))

    formatted = format_debug_exception(
        error,
        secrets=[api_key],
        context=f"processing with {api_key}",
    )

    assert "Traceback (most recent call last)" in formatted
    assert "RuntimeError" in formatted
    assert "Context: processing with [REDACTED]" in formatted
    assert api_key not in formatted


def test_debug_exception_includes_safe_public_sdk_diagnostics() -> None:
    api_key = "sdk-secret-key"
    error = FakeSdkError(
        status_code=422,
        message=f"invalid key {api_key}",
        body={"detail": f"request contained {api_key}"},
    )
    error.headers = {"authorization": f"Bearer {api_key}"}

    formatted = format_debug_exception(error, secrets=[api_key])

    assert "Status code: 422" in formatted
    assert "SDK message:" in formatted
    assert "Response body:" in formatted
    assert "Headers:" in formatted
    assert "request contained [REDACTED]" in formatted
    assert api_key not in formatted


def test_exit_code_constants_are_stable() -> None:
    assert errors_module.EXIT_FAILURE == 1
    assert errors_module.EXIT_USAGE == 2
    assert errors_module.EXIT_SETUP == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (errors_module.InputError("x"), "input_error"),
        (ConfigError("x"), "config_error"),
        (ApiError("x", status_code=429), "api_error"),
        (errors_module.PersistenceError("x"), "persistence_error"),
        (MistralCliError("x"), "unexpected_error"),
    ],
)
def test_error_code_maps_taxonomy(error: MistralCliError, expected: str) -> None:
    assert errors_module.error_code(error) == expected
