from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO

import pytest
from rich.console import Console

from mistral_cli.console import (
    ConsoleBundle,
    create_console_bundle,
    sanitize_terminal_text,
)


def _console(
    stream: StringIO,
    *,
    force_terminal: bool = False,
    width: int = 100,
) -> Console:
    return Console(file=stream, force_terminal=force_terminal, width=width)


class FlushTrackingStringIO(StringIO):
    flushed = False

    def flush(self) -> None:
        self.flushed = True
        super().flush()


def _assert_no_unsafe_controls(text: str) -> None:
    for character in text:
        codepoint = ord(character)
        assert character in "\n\t" or (
            codepoint >= 0x20 and codepoint != 0x7F and not 0x80 <= codepoint <= 0x9F
        )


def test_sanitize_terminal_text_removes_terminal_controls() -> None:
    untrusted = (
        "safe\tline\n"
        "\x1b[31mred\x1b[0m"
        "\x1b]0;owned-title\x07visible"
        "\x1b]8;;https://evil.example\x1b\\link\x1b]8;;\x1b\\"
        "\x1bPprivate-dcs\x1b\\"
        "\x1bcreset"
        "\rreturn\bback\x00\x7f"
        "\x9b2Jafter-csi"
        "\x90private-c1-dcs\x9c"
        "\x85unicode-雪"
    )

    sanitized = sanitize_terminal_text(untrusted)

    assert sanitized == ("safe\tline\nredvisiblelinkresetreturnbackafter-csiunicode-雪")
    _assert_no_unsafe_controls(sanitized)


def test_write_stdout_preserves_raw_payload_without_newline_or_wrapping() -> None:
    stdout = FlushTrackingStringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(
        stdout=_console(stdout, width=20),
        stderr=_console(stderr, width=20),
    )
    payload = (
        "# Markdown\n"
        + "a" * 80
        + '\n{"value":"unchanged"}'
        + "\x1b]0;hidden-title\x07"
    )

    consoles.write_stdout(payload)

    assert stdout.getvalue() == "# Markdown\n" + "a" * 80 + '\n{"value":"unchanged"}'
    assert not stdout.getvalue().endswith("\n")
    assert stdout.flushed is True
    assert stderr.getvalue() == ""


def test_console_bundle_routes_output_to_the_correct_stream() -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=_console(stdout), stderr=_console(stderr))

    consoles.print("result.md")
    consoles.print_status("Processing result.md")
    consoles.print_error("Could not process result.md")

    assert stdout.getvalue() == "result.md\n"
    assert stderr.getvalue() == ("Processing result.md\nCould not process result.md\n")


def test_status_context_never_writes_to_stdout() -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=_console(stdout), stderr=_console(stderr))

    with consoles.status("Processing [untrusted].pdf"):
        consoles.print_status("still working")

    assert stdout.getvalue() == ""
    assert "still working" in stderr.getvalue()


def test_nonterminal_output_has_no_ansi_and_does_not_interpret_markup() -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=_console(stdout), stderr=_console(stderr))

    consoles.print("[bold]not markup[/bold] résumé.txt")
    consoles.print_error("[red]literal error[/red]")

    assert stdout.getvalue() == "[bold]not markup[/bold] résumé.txt\n"
    assert stderr.getvalue() == "[red]literal error[/red]\n"
    assert "\x1b[" not in stdout.getvalue() + stderr.getvalue()


@pytest.mark.parametrize("force_terminal", [False, True])
def test_all_human_output_sanitizes_untrusted_controls(
    force_terminal: bool,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(
        stdout=_console(stdout, force_terminal=force_terminal),
        stderr=_console(stderr, force_terminal=force_terminal),
    )
    untrusted = "report\x1b[2J.pdf\x1b]0;hijacked\x07\rrewritten"

    consoles.print(untrusted)
    consoles.print_status(untrusted)
    consoles.print_error(untrusted)

    assert stdout.getvalue() == "report.pdfrewritten\n"
    assert stderr.getvalue() == "report.pdfrewritten\nreport.pdfrewritten\n"
    _assert_no_unsafe_controls(stdout.getvalue() + stderr.getvalue())


def test_status_sanitizes_message_before_passing_it_to_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    stderr_console = _console(stderr)
    consoles = ConsoleBundle(stdout=_console(stdout), stderr=stderr_console)
    received: list[str] = []

    @contextmanager
    def recording_status(message: object) -> Generator[None]:
        received.append(str(message))
        yield

    monkeypatch.setattr(stderr_console, "status", recording_status)

    with consoles.status("loading\x1b]0;owned\x07 safe.pdf\r"):
        pass

    assert received == ["loading safe.pdf"]
    assert stdout.getvalue() == ""


def _raise_runtime_error(message: str) -> None:
    raise RuntimeError(message)


@pytest.mark.parametrize("force_terminal", [False, True])
def test_debug_exception_is_redacted_and_only_written_to_stderr(
    force_terminal: bool,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(
        stdout=_console(stdout, force_terminal=force_terminal),
        stderr=_console(stderr, force_terminal=force_terminal),
    )
    api_key = "secret-api-key"
    terminal_attack = "".join(("\x1b]", "0;", "stolen-title", "\x07", "\r"))

    try:
        _raise_runtime_error(
            f"response body includes {api_key}{terminal_attack}rewritten"
        )
    except RuntimeError as error:
        consoles.print_debug_exception(
            error,
            secrets=[api_key],
            context=f"request used {api_key}",
        )

    assert stdout.getvalue() == ""
    assert "Traceback (most recent call last)" in stderr.getvalue()
    assert "RuntimeError" in stderr.getvalue()
    assert api_key not in stderr.getvalue()
    assert "stolen-title" not in stderr.getvalue()
    _assert_no_unsafe_controls(stderr.getvalue())


def test_production_factory_creates_distinct_standard_consoles() -> None:
    consoles = create_console_bundle()

    assert consoles.stdout is not consoles.stderr
    assert consoles.stderr.stderr is True
