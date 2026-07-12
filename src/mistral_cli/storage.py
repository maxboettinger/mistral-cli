from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from mistral_cli import __version__
from mistral_cli.errors import PersistenceError
from mistral_cli.formatters import build_envelope, serialize_json
from mistral_cli.models import ApiResult, Operation, OutputFormat, SavedResult

_FALLBACK_NAME_MAX = 255
_MAX_PRESERVED_EXTENSION_BYTES = 16


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_cli_version() -> str:
    return __version__


def _set_private_mode(fd: int, path: Path) -> None:
    if os.name != "posix":
        return
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(fd, 0o600)
    else:
        path.chmod(0o600)


def _write_temp_file(content: bytes, directory: Path) -> Path:
    """Write content to a private, fully synced temp file inside directory."""
    fd, temp_name = tempfile.mkstemp(
        prefix=".mistral-cli-",
        suffix=".tmp",
        dir=directory,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output:
            _set_private_mode(output.fileno(), temp_path)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _unlink_quietly(paths: Iterable[Path]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _link_into_place(
    temps: dict[str, Path],
    destinations: dict[str, Path],
) -> bool:
    """Atomically link every artifact into place without overwriting.

    Returns False when any destination name is already taken so the caller
    can retry with the next collision suffix.
    """
    created: list[Path] = []
    for extension, destination in destinations.items():
        try:
            os.link(temps[extension], destination)
        except FileExistsError:
            _unlink_quietly(created)
            return False
        except OSError:
            _unlink_quietly(created)
            raise
        created.append(destination)
    return True


def _name_max(directory: Path) -> int:
    pathconf = getattr(os, "pathconf", None)
    if callable(pathconf):
        try:
            value = pathconf(directory, "PC_NAME_MAX")
        except (OSError, TypeError, ValueError):
            pass
        else:
            if isinstance(value, int) and value > 0:
                return min(value, _FALLBACK_NAME_MAX)
    return _FALLBACK_NAME_MAX


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _fit_source_filename(filename: str, byte_limit: int) -> str:
    if len(filename.encode("utf-8")) <= byte_limit:
        return filename

    extension = Path(filename).suffix
    extension_size = len(extension.encode("utf-8"))
    if (
        extension
        and extension_size <= _MAX_PRESERVED_EXTENSION_BYTES
        and extension_size < byte_limit
    ):
        stem = filename[: -len(extension)]
        return _truncate_utf8(stem, byte_limit - extension_size) + extension
    return _truncate_utf8(filename, byte_limit)


def _candidate_destinations(
    *,
    directory: Path,
    timestamp: str,
    suffix: int,
    source_filename: str,
    extensions: tuple[str, ...],
    name_max: int,
) -> dict[str, Path]:
    counter = "" if suffix == 0 else f"-{suffix}"
    prefix = f"{timestamp}{counter}-"
    byte_limit = (
        name_max
        - len(prefix.encode("utf-8"))
        - max(len(extension.encode("utf-8")) for extension in extensions)
    )
    if byte_limit <= 0:
        raise OSError("filesystem name limit is too small for result names")
    fitted_filename = _fit_source_filename(source_filename, byte_limit)
    base_name = f"{prefix}{fitted_filename}"
    return {
        extension: directory / f"{base_name}{extension}" for extension in extensions
    }


class ResultStore:
    def __init__(
        self,
        base_dir: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
        version: Callable[[], str] = get_cli_version,
    ) -> None:
        self._base_dir = (
            Path("~/.mistral").expanduser() if base_dir is None else base_dir
        )
        self._clock = clock
        self._version = version

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save(
        self,
        result: ApiResult,
        markdown: str,
        output_format: OutputFormat,
        output_dir: Path | None = None,
    ) -> SavedResult:
        saved_at = self._clock()
        if saved_at.utcoffset() is None:
            raise PersistenceError("Save clock must return a timezone-aware datetime.")
        timestamp = saved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")

        if output_format is OutputFormat.MD:
            extensions = (".md",)
        elif output_format is OutputFormat.JSON:
            extensions = (".json",)
        elif output_format is OutputFormat.BOTH:
            extensions = (".md", ".json")
        else:
            raise PersistenceError(f"Unsupported output format: {output_format!s}.")

        contents: dict[str, bytes] = {}
        if ".md" in extensions:
            try:
                contents[".md"] = markdown.encode("utf-8")
            except UnicodeError as error:
                raise PersistenceError(
                    f"Could not encode result Markdown as UTF-8: {error}."
                ) from error
        if ".json" in extensions:
            try:
                contents[".json"] = serialize_json(
                    build_envelope(result, self._version())
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError) as error:
                raise PersistenceError(
                    f"Could not serialize result JSON: {error}."
                ) from error

        destination_dir = output_dir or (
            self._base_dir
            / ("ocr" if result.operation is Operation.OCR else "transcriptions")
        )
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PersistenceError(
                f"Could not create result directory '{destination_dir}': {error}."
            ) from error

        name_max = _name_max(destination_dir)
        temps: dict[str, Path] = {}
        try:
            try:
                for extension in extensions:
                    temps[extension] = _write_temp_file(
                        contents[extension], destination_dir
                    )
            except OSError as error:
                raise PersistenceError(
                    f"Could not save result in '{destination_dir}': {error}."
                ) from error

            suffix = 0
            while True:
                try:
                    destinations = _candidate_destinations(
                        directory=destination_dir,
                        timestamp=timestamp,
                        suffix=suffix,
                        source_filename=result.source.filename,
                        extensions=extensions,
                        name_max=name_max,
                    )
                except OSError as error:
                    raise PersistenceError(
                        f"Could not prepare result path in "
                        f"'{destination_dir}': {error}."
                    ) from error
                try:
                    published = _link_into_place(temps, destinations)
                except OSError as error:
                    raise PersistenceError(
                        f"Could not save result in '{destination_dir}': {error}."
                    ) from error
                if published:
                    return SavedResult(
                        markdown=destinations.get(".md"),
                        json=destinations.get(".json"),
                    )
                suffix += 1
        finally:
            _unlink_quietly(temps.values())
