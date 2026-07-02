from __future__ import annotations

from io import StringIO

from rich.console import Console

from mistral_cli.console import ConsoleBundle, create_console_bundle


def _console(stream: StringIO) -> Console:
    return Console(file=stream, force_terminal=False, width=100)


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


def test_debug_exception_is_redacted_and_only_written_to_stderr() -> None:
    stdout = StringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=_console(stdout), stderr=_console(stderr))
    api_key = "secret-api-key"

    try:
        raise RuntimeError(f"response body includes {api_key}")
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


def test_production_factory_creates_distinct_standard_consoles() -> None:
    consoles = create_console_bundle()

    assert consoles.stdout is not consoles.stderr
    assert consoles.stderr.stderr is True
