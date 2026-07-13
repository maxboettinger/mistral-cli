from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

import click

from moxtral.formatters import serialize_json
from moxtral.schema import record_schema

if TYPE_CHECKING:
    from moxtral.cli.main import AppContext


@click.command()
@click.option(
    "--schema",
    "show_schema",
    is_flag=True,
    help="Print the JSON Schema describing --json output records.",
)
@click.pass_obj
def agent(context: AppContext, show_schema: bool) -> None:
    """Print agent-oriented usage documentation for this CLI."""
    if show_schema:
        context.consoles.write_stdout(serialize_json(record_schema()))
        return
    guide = (
        resources.files("moxtral")
        .joinpath("data/agent_guide.md")
        .read_text(encoding="utf-8")
    )
    context.consoles.write_stdout(guide)
