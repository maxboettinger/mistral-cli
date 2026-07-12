from __future__ import annotations

import sys
from io import StringIO

from mistral_cli.console import (
    ConsoleBundle,
    create_console_bundle,
    sanitize_terminal_text,
)


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


def test_write_stdout_preserves_raw_payload_and_flushes() -> None:
    stdout = FlushTrackingStringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=stdout, stderr=stderr)
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


def test_write_stderr_routes_to_stderr_only_and_sanitizes() -> None:
    stdout = StringIO()
    stderr = FlushTrackingStringIO()
    consoles = ConsoleBundle(stdout=stdout, stderr=stderr)
    untrusted = "report\x1b[2J.pdf\x1b]0;hijacked\x07\rrewritten\n"

    consoles.write_stderr(untrusted)

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "report.pdfrewritten\n"
    assert stderr.flushed is True
    _assert_no_unsafe_controls(stderr.getvalue())


def test_production_factory_uses_the_process_standard_streams() -> None:
    consoles = create_console_bundle()

    assert consoles.stdout is sys.stdout
    assert consoles.stderr is sys.stderr
