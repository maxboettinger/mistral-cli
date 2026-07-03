from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import click

from mistral_cli.config import ConfigStore
from mistral_cli.console import sanitize_terminal_text
from mistral_cli.errors import ConfigError, redact, translate_exception
from mistral_cli.models import ApiResult, InputSource, JSONMapping, JSONValue


class CommandConsoles(Protocol):
    def write_stderr(self, payload: str) -> None: ...

    def print_debug_exception(
        self,
        error: Exception,
        *,
        secrets: tuple[str, ...],
        context: str | None = None,
    ) -> None: ...


class CommandContext(Protocol):
    @property
    def config_path(self) -> Path: ...

    @property
    def debug(self) -> bool: ...

    @property
    def consoles(self) -> CommandConsoles: ...


def safe_terminal_text(text: str, secrets: tuple[str, ...]) -> str:
    """Redact secrets before removing terminal control sequences."""
    return sanitize_terminal_text(redact(text, secrets))


def candidate_secrets(context: CommandContext) -> tuple[str, ...]:
    """Collect potential keys without allowing config errors to mask input errors."""
    secrets: list[str] = []
    environment_key = os.environ.get("MISTRAL_API_KEY")
    if environment_key is not None and environment_key.strip():
        secrets.append(environment_key)

    try:
        configured_key = ConfigStore(context.config_path, environ={}).load().api_key
    except ConfigError:
        configured_key = None
    if configured_key is not None:
        secrets.append(configured_key)
    return tuple(dict.fromkeys(secrets))


def extend_secrets(secrets: tuple[str, ...], secret: str) -> tuple[str, ...]:
    """Append a newly resolved secret while preserving order and uniqueness."""
    if secret in secrets:
        return secrets
    return (*secrets, secret)


def _redact_json_value(
    value: JSONValue,
    secrets: tuple[str, ...],
) -> JSONValue:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_redact_json_value(item, secrets) for item in value]
    if isinstance(value, Mapping):
        return _redact_json_mapping(cast(JSONMapping, value), secrets)
    return value


def _redact_json_mapping(
    mapping: JSONMapping,
    secrets: tuple[str, ...],
) -> dict[str, JSONValue]:
    redacted_mapping: dict[str, JSONValue] = {}
    for key, value in mapping.items():
        safe_key = redact(key, secrets)
        unique_key = safe_key
        suffix = 1
        while unique_key in redacted_mapping:
            unique_key = f"{safe_key}#{suffix}"
            suffix += 1
        redacted_mapping[unique_key] = _redact_json_value(value, secrets)
    return redacted_mapping


def _redact_source(
    source: InputSource,
    secrets: tuple[str, ...],
) -> InputSource:
    safe_path = None if source.path is None else Path(redact(str(source.path), secrets))
    return InputSource(
        kind=source.kind,
        value=redact(source.value, secrets),
        filename=redact(source.filename, secrets),
        path=safe_path,
        ocr_kind=source.ocr_kind,
    )


def redact_result(
    result: ApiResult,
    secrets: tuple[str, ...],
) -> ApiResult:
    """Return an API result with secrets recursively removed."""
    return ApiResult(
        operation=result.operation,
        source=_redact_source(result.source, secrets),
        request_metadata=_redact_json_mapping(result.request_metadata, secrets),
        response=_redact_json_mapping(result.response, secrets),
        created_at=result.created_at,
    )


def report_error(
    context: CommandContext,
    error: Exception,
    *,
    secrets: tuple[str, ...],
    setup_debug_context: str,
    source_debug_prefix: str,
    source: str | None = None,
) -> None:
    """Translate and safely report a setup or per-source command error."""
    translated = translate_exception(error)
    line = (
        f"Setup error: {translated}\n"
        if source is None
        else f"{source}: {translated}\n"
    )
    debug_context = (
        setup_debug_context if source is None else f"{source_debug_prefix}: {source}"
    )
    context.consoles.write_stderr(safe_terminal_text(line, secrets))
    if context.debug:
        context.consoles.print_debug_exception(
            error,
            secrets=secrets,
            context=debug_context,
        )


def resolve_api_key(
    context: CommandContext,
    secrets: tuple[str, ...],
    *,
    setup_debug_context: str,
) -> tuple[str, tuple[str, ...]]:
    """Resolve the API key or emit the shared safe setup failure."""
    try:
        api_key = ConfigStore(context.config_path).resolve_api_key()
    except ConfigError as error:
        report_error(
            context,
            error,
            secrets=secrets,
            setup_debug_context=setup_debug_context,
            source_debug_prefix="",
        )
        raise click.exceptions.Exit(1) from error
    return api_key, extend_secrets(secrets, api_key)
