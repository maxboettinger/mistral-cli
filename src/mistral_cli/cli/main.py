from dataclasses import dataclass
from pathlib import Path

import click

from mistral_cli import __version__

DEFAULT_CONFIG_PATH = Path("~/.mistral/config.toml").expanduser()


@dataclass(frozen=True, slots=True)
class AppContext:
    config_path: Path
    debug: bool


@click.group()
@click.version_option(version=__version__)
@click.option("--debug", is_flag=True, help="Show detailed error information.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to the configuration file.",
)
@click.pass_context
def cli(ctx: click.Context, debug: bool, config_path: Path) -> None:
    """Work with Mistral OCR and audio transcription."""
    ctx.obj = AppContext(config_path=config_path, debug=debug)


@cli.group()
def config() -> None:
    """Manage CLI configuration."""


@cli.command()
def ocr() -> None:
    """Extract text from a document or image."""


@cli.command()
def transcribe() -> None:
    """Transcribe audio into text."""
