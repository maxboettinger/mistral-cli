from __future__ import annotations

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

from mistral_cli.errors import format_debug_exception


@dataclass(frozen=True, slots=True)
class ConsoleBundle:
    """Application output streams and their safe rendering helpers."""

    stdout: Console
    stderr: Console

    def print(self, message: str) -> None:
        self.stdout.print(Text(message))

    def print_status(self, message: str) -> None:
        self.stderr.print(Text(message))

    def print_error(self, message: str) -> None:
        self.stderr.print(Text(message))

    @contextmanager
    def status(self, message: str) -> Generator[None]:
        with self.stderr.status(Text(message)):
            yield

    def print_debug_exception(
        self,
        error: Exception,
        *,
        secrets: Iterable[str] = (),
        context: str | None = None,
    ) -> None:
        formatted = format_debug_exception(
            error,
            secrets=secrets,
            context=context,
        )
        self.stderr.print(Text(formatted), end="")


def create_console_bundle() -> ConsoleBundle:
    """Create consoles using Rich's standard terminal detection."""
    return ConsoleBundle(
        stdout=Console(),
        stderr=Console(stderr=True),
    )
