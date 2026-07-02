from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click
import tomli_w

from mistral_cli.config import ConfigStore
from mistral_cli.errors import ConfigError

if TYPE_CHECKING:
    from mistral_cli.cli.main import AppContext


@click.group()
def config() -> None:
    """Manage CLI configuration."""


@config.command("set")
@click.argument("name", type=click.Choice(["api-key"]))
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read the value from standard input.",
)
@click.pass_obj
def set_value(context: AppContext, name: str, read_stdin: bool) -> None:
    """Set a configuration value."""
    if read_stdin:
        value = sys.stdin.read().rstrip("\r\n")
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

    try:
        ConfigStore(context.config_path).set(name, value)
    except ConfigError as error:
        raise click.ClickException(str(error)) from error

    click.echo("Configuration updated.")


@config.command("show")
@click.pass_obj
def show(context: AppContext) -> None:
    """Show configuration with secrets redacted."""
    try:
        values = ConfigStore(context.config_path).redacted()
    except ConfigError as error:
        raise click.ClickException(str(error)) from error

    click.echo(tomli_w.dumps(values), nl=False)


@config.command("unset")
@click.argument("name", type=click.Choice(["api-key"]))
@click.pass_obj
def unset(context: AppContext, name: str) -> None:
    """Remove a configuration value."""
    try:
        removed = ConfigStore(context.config_path).unset(name)
    except ConfigError as error:
        raise click.ClickException(str(error)) from error

    if removed:
        click.echo(f"{name} removed.")
    else:
        click.echo(f"{name} already absent.")


@config.command("path")
@click.pass_obj
def show_path(context: AppContext) -> None:
    """Print the effective configuration path."""
    click.echo(context.config_path)
