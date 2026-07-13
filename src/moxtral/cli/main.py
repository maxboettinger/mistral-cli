from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from moxtral import __version__
from moxtral.cli.agent import agent
from moxtral.cli.config import config
from moxtral.cli.ocr import ocr
from moxtral.cli.transcribe import transcribe
from moxtral.console import ConsoleBundle, create_console_bundle

DEFAULT_CONFIG_PATH = Path("~/.mistral/config.toml").expanduser()


@dataclass(frozen=True, slots=True)
class AppContext:
    config_path: Path
    debug: bool
    consoles: ConsoleBundle


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "Run 'mistral agent' for agent-oriented usage docs and "
        "'mistral agent --schema' for the JSON output schema."
    ),
)
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
    ctx.obj = AppContext(
        config_path=config_path,
        debug=debug,
        consoles=create_console_bundle(),
    )


cli.add_command(agent)
cli.add_command(config)
cli.add_command(ocr)
cli.add_command(transcribe)
