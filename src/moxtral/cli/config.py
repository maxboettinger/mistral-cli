from __future__ import annotations

import sys
from typing import TYPE_CHECKING, NoReturn, cast

import click
import tomli_w

from moxtral.cli.common import (
    candidate_secrets,
    extend_secrets,
    safe_terminal_text,
    write_debug_exception,
)
from moxtral.config import ConfigStore
from moxtral.errors import ConfigError

if TYPE_CHECKING:
    from moxtral.cli.main import AppContext


@click.group()
def config() -> None:
    """Manage CLI configuration."""


def _raise_config_error(
    context: AppContext,
    error: ConfigError,
    *,
    secrets: tuple[str, ...],
    debug_context: str,
) -> NoReturn:
    if context.debug:
        write_debug_exception(
            context,
            error,
            secrets=secrets,
            debug_context=debug_context,
        )
    raise click.ClickException(safe_terminal_text(str(error), secrets)) from error


@config.command(
    "set",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    },
)
@click.argument("name", type=click.Choice(["api-key"]))
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read the value from standard input.",
)
@click.pass_context
def set_value(context: click.Context, name: str, read_stdin: bool) -> None:
    """Set a configuration value."""
    if context.args:
        raise click.UsageError(
            "API keys must be provided through the prompt or --stdin.",
            ctx=context,
        )

    if read_stdin:
        try:
            value = sys.stdin.read().rstrip("\r\n")
        except UnicodeError as error:
            raise click.ClickException(
                "Could not read API key from standard input."
            ) from error
        if not value:
            raise click.ClickException("Standard input must not be empty.")
        if "\r" in value or "\n" in value:
            raise click.ClickException("Standard input must contain a single line.")
    else:
        value = click.prompt(
            "API key",
            hide_input=True,
            confirmation_prompt=True,
        )

    app_context = cast("AppContext", context.obj)
    try:
        ConfigStore(app_context.config_path).set(name, value)
    except ConfigError as error:
        secrets = extend_secrets(candidate_secrets(app_context), value)
        _raise_config_error(
            app_context,
            error,
            secrets=secrets,
            debug_context=f"setting configuration {name}",
        )

    click.echo("Configuration updated.")


@config.command("show")
@click.pass_obj
def show(context: AppContext) -> None:
    """Show configuration with secrets redacted."""
    try:
        values = ConfigStore(context.config_path).redacted()
    except ConfigError as error:
        _raise_config_error(
            context,
            error,
            secrets=candidate_secrets(context),
            debug_context="showing configuration",
        )

    click.echo(tomli_w.dumps(values), nl=False)


@config.command("unset")
@click.argument("name", type=click.Choice(["api-key"]))
@click.pass_obj
def unset(context: AppContext, name: str) -> None:
    """Remove a configuration value."""
    try:
        removed = ConfigStore(context.config_path).unset(name)
    except ConfigError as error:
        _raise_config_error(
            context,
            error,
            secrets=candidate_secrets(context),
            debug_context=f"unsetting configuration {name}",
        )

    if removed:
        click.echo(f"{name} removed.")
    else:
        click.echo(f"{name} already absent.")


@config.command("path")
@click.pass_obj
def show_path(context: AppContext) -> None:
    """Print the effective configuration path."""
    click.echo(context.config_path)
