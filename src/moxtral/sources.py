from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

from moxtral.errors import InputError
from moxtral.models import InputSource, OcrSourceKind, Operation, SourceKind

_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_UNSAFE_FILENAME_CHARACTERS = re.compile(r'[\x00-\x1f\x7f-\x9f<>:"/\\|?*]')
_MAX_INLINE_DOCUMENT_BYTES = 50 * 1024 * 1024
_IMAGE_URL_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heif",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


def _is_audio_purpose(purpose: str | Operation) -> bool:
    if purpose in ("audio", Operation.TRANSCRIPTION):
        return True
    if purpose in ("document", Operation.OCR):
        return False
    raise InputError("source purpose must be OCR/document or transcription/audio.")


def _fallback_filename(purpose: str | Operation) -> str:
    return "remote-audio" if _is_audio_purpose(purpose) else "remote-document"


def _sanitize_filename(filename: str, fallback: str) -> str:
    sanitized = _UNSAFE_FILENAME_CHARACTERS.sub("_", filename).rstrip(" .")
    if not sanitized or all(character == "." for character in sanitized):
        return fallback

    stem = sanitized.split(".", maxsplit=1)[0].rstrip(" ")
    if stem.casefold() in _WINDOWS_RESERVED_STEMS:
        return f"_{sanitized}"
    return sanitized


def _url_ocr_kind(filename: str) -> OcrSourceKind:
    if Path(filename).suffix.casefold() in _IMAGE_URL_SUFFIXES:
        return OcrSourceKind.IMAGE
    return OcrSourceKind.DOCUMENT


def _local_ocr_kind(path: Path) -> OcrSourceKind:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is not None and mime_type.casefold().startswith("image/"):
        return OcrSourceKind.IMAGE
    return OcrSourceKind.DOCUMENT


def _resolve_url(
    value: str,
    purpose: str | Operation,
    parsed: SplitResult,
) -> InputSource:
    fallback = _fallback_filename(purpose)
    encoded_component = parsed.path.rsplit("/", maxsplit=1)[-1]
    filename = _sanitize_filename(unquote(encoded_component), fallback)
    return InputSource(
        kind=SourceKind.URL,
        value=value,
        filename=filename,
        ocr_kind=_url_ocr_kind(filename),
    )


def _resolve_local(value: str, purpose: str | Operation) -> InputSource:
    audio = _is_audio_purpose(purpose)
    path = Path(value)
    try:
        path = path.expanduser()
        if not path.exists():
            raise InputError(f"source path does not exist: {path}")
        if not path.is_file():
            raise InputError(f"source path is not a regular file: {path}")
        if not audio:
            size = path.stat().st_size
            if size > _MAX_INLINE_DOCUMENT_BYTES:
                raise InputError(
                    f"source file is {size} bytes; local OCR sources are sent "
                    "inline as base64 and are limited to 50 MB. Reduce the "
                    "document or host it at an HTTP(S) URL instead."
                )
        with path.open("rb"):
            pass
    except InputError:
        raise
    except (OSError, RuntimeError) as error:
        raise InputError(f"source path could not be read: {path}: {error}") from error

    filename = _sanitize_filename(path.name, "local-file")
    return InputSource(
        kind=SourceKind.FILE,
        value=str(path),
        filename=filename,
        path=path,
        ocr_kind=_local_ocr_kind(path),
    )


def resolve_source(value: str, purpose: str | Operation) -> InputSource:
    if _WINDOWS_DRIVE_PATH.match(value) is not None:
        return _resolve_local(value, purpose)

    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise InputError(f"source URL is invalid: {error}") from error

    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        try:
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            raise InputError(f"source URL is invalid: {error}") from error
        if (
            not parsed.netloc
            or hostname is None
            or not hostname
            or any(character.isspace() for character in hostname)
        ):
            raise InputError(
                "source HTTP(S) URL must include a valid authority and hostname."
            )
        return _resolve_url(value, purpose, parsed)
    if scheme:
        raise InputError(
            f"source URL scheme {parsed.scheme!r} is unsupported; "
            "supported schemes are http and https."
        )
    return _resolve_local(value, purpose)
