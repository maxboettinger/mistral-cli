from __future__ import annotations

import traceback
from collections.abc import Iterable

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_SETUP = 3


class MoxtralError(Exception):
    """Base exception for expected moxtral failures."""


class ApiError(MoxtralError):
    """Raised when a Mistral API request fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConfigError(MoxtralError):
    """Raised when configuration cannot be read, validated, or updated."""


class InputError(MoxtralError):
    """Raised when command input cannot be resolved or validated."""


class PersistenceError(MoxtralError):
    """Raised when a result cannot be serialized or saved safely."""


def _safe_attribute(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _valid_status_code(value: object) -> int | None:
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _safe_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        return "<unprintable>"


def _status_code(error: Exception) -> int | None:
    candidates = [
        error,
        _safe_attribute(error, "raw_response"),
        _safe_attribute(error, "response"),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        for name in ("status_code", "status"):
            status_code = _valid_status_code(_safe_attribute(candidate, name))
            if status_code is not None:
                return status_code
    return None


def _is_timeout(error: Exception) -> bool:
    import httpx

    return isinstance(error, (TimeoutError, httpx.TimeoutException))


def _is_network_error(error: Exception) -> bool:
    import httpx

    return isinstance(error, (ConnectionError, httpx.NetworkError))


def _api_error(status_code: int) -> ApiError:
    if status_code == 401:
        message = "Authentication failed. Check your Mistral API key and configuration."
    elif status_code == 403:
        message = (
            "Permission denied. Check your Mistral API key and account permissions."
        )
    elif status_code == 429:
        message = "Mistral rate limit reached. Wait and retry the request."
    elif status_code == 422:
        message = "Invalid API request. Check the supplied options and try again."
    elif 400 <= status_code <= 499:
        message = f"Mistral rejected the request (HTTP {status_code})."
    elif 500 <= status_code <= 599:
        message = (
            f"Mistral server error (HTTP {status_code}). Wait and retry the request."
        )
    else:
        message = "The Mistral API request failed. Try again."
    return ApiError(message, status_code=status_code)


def translate_exception(error: Exception) -> MoxtralError:
    """Translate external failures without exposing untrusted exception details."""
    if isinstance(error, MoxtralError):
        return error
    if _is_timeout(error):
        return ApiError("The Mistral request timed out. Try again.")
    if _is_network_error(error):
        return ApiError(
            "Could not connect to Mistral. Check your network and try again."
        )

    status_code = _status_code(error)
    if status_code is not None:
        return _api_error(status_code)
    return MoxtralError("Unexpected failure. Run again with --debug for details.")


def error_code(error: MoxtralError) -> str:
    """Return the stable machine-readable code for a translated error."""
    if isinstance(error, InputError):
        return "input_error"
    if isinstance(error, ConfigError):
        return "config_error"
    if isinstance(error, ApiError):
        return "api_error"
    if isinstance(error, PersistenceError):
        return "persistence_error"
    return "unexpected_error"


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each known, nonblank secret."""
    known_secrets = sorted(
        {secret for secret in secrets if secret.strip()},
        key=len,
        reverse=True,
    )
    for secret in known_secrets:
        text = text.replace(secret, "[REDACTED]")
    return text


def format_debug_exception(
    error: Exception,
    *,
    secrets: Iterable[str] = (),
    context: str | None = None,
) -> str:
    """Format diagnostic exception detail, redacting known secrets."""
    sections: list[str] = []
    if context is not None:
        sections.append(f"Context: {context}\n")
    error_type = type(error)
    sections.append(
        f"Exception type: {error_type.__module__}.{error_type.__qualname__}\n"
    )

    status_code = _status_code(error)
    if status_code is not None:
        sections.append(f"Status code: {status_code}\n")
    for label, attribute in (
        ("SDK message", "message"),
        ("Response body", "body"),
        ("Headers", "headers"),
    ):
        value = _safe_attribute(error, attribute)
        if value is not None:
            sections.append(f"{label}: {_safe_repr(value)}\n")

    sections.extend(
        traceback.format_exception(
            error_type,
            error,
            error.__traceback__,
        )
    )
    return redact("".join(sections), secrets)
