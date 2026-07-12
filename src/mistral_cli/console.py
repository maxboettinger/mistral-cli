from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

_ESC = "\x1b"
_BEL = "\x07"
_C1_CSI = "\x9b"
_C1_ST = "\x9c"
_STRING_CONTROL_STARTS = {"P", "X", "]", "^", "_"}
_C1_STRING_CONTROL_STARTS = {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}


def _consume_control_string(text: str, start: int, *, osc: bool) -> int:
    index = start
    while index < len(text):
        character = text[index]
        if character == _C1_ST or (osc and character == _BEL):
            return index + 1
        if character == _ESC and index + 1 < len(text) and text[index + 1] == "\\":
            return index + 2
        index += 1
    return len(text)


def _consume_csi(text: str, start: int) -> int:
    index = start
    while index < len(text):
        codepoint = ord(text[index])
        if 0x40 <= codepoint <= 0x7E:
            return index + 1
        if 0x20 <= codepoint <= 0x3F:
            index += 1
            continue
        return index
    return len(text)


def _consume_escape(text: str, start: int) -> int:
    index = start + 1
    if index >= len(text):
        return index

    character = text[index]
    if character == "[":
        return _consume_csi(text, index + 1)
    if character in _STRING_CONTROL_STARTS:
        return _consume_control_string(
            text,
            index + 1,
            osc=character == "]",
        )

    codepoint = ord(character)
    while 0x20 <= codepoint <= 0x2F:
        index += 1
        if index >= len(text):
            return index
        codepoint = ord(text[index])
    if 0x30 <= codepoint <= 0x7E:
        return index + 1
    return start + 1


def sanitize_terminal_text(text: str) -> str:
    """Remove terminal control sequences while preserving ordinary text."""
    sanitized: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        codepoint = ord(character)

        if character == _ESC:
            index = _consume_escape(text, index)
            continue
        if character == _C1_CSI:
            index = _consume_csi(text, index + 1)
            continue
        if character in _C1_STRING_CONTROL_STARTS:
            index = _consume_control_string(
                text,
                index + 1,
                osc=character == "\x9d",
            )
            continue
        if character in {"\n", "\t"}:
            sanitized.append(character)
        elif codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            pass
        else:
            sanitized.append(character)
        index += 1
    return "".join(sanitized)


class TextStream(Protocol):
    """The minimal writable text stream ConsoleBundle needs."""

    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ConsoleBundle:
    """Application output streams behind sanitizing write helpers."""

    stdout: TextStream
    stderr: TextStream

    def write_stdout(self, payload: str) -> None:
        self.stdout.write(sanitize_terminal_text(payload))
        self.stdout.flush()

    def write_stderr(self, payload: str) -> None:
        self.stderr.write(sanitize_terminal_text(payload))
        self.stderr.flush()


def create_console_bundle() -> ConsoleBundle:
    """Create the bundle over the process standard streams."""
    return ConsoleBundle(stdout=sys.stdout, stderr=sys.stderr)
