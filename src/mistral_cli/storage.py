from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mistral_cli import __version__
from mistral_cli.errors import PersistenceError
from mistral_cli.formatters import build_envelope, serialize_json
from mistral_cli.models import ApiResult, Operation, OutputFormat, SavedResult

FileIdentity = tuple[int, int]


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_cli_version() -> str:
    return __version__


def _write_all(fd: int, content: bytes) -> None:
    with os.fdopen(fd, "wb", closefd=False) as output:
        output.write(content)
        output.flush()
        os.fsync(fd)


def _publish(content: bytes, destination: Path) -> FileIdentity:
    fd = -1
    temp_path: Path | None = None
    linked_identity: FileIdentity | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".mistral-cli-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temp_path = Path(temp_name)
        os.fchmod(fd, 0o600)
        _write_all(fd, content)
        stat_result = os.fstat(fd)
        identity = stat_result.st_dev, stat_result.st_ino
        os.close(fd)
        fd = -1
        os.link(temp_path, destination)
        linked_identity = identity
        return identity
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                if linked_identity is not None:
                    _remove_created(destination, linked_identity)
                raise


def _remove_created(path: Path, identity: FileIdentity) -> None:
    try:
        stat_result = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if (stat_result.st_dev, stat_result.st_ino) == identity:
        path.unlink()


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

        suffix = 0
        while True:
            counter = "" if suffix == 0 else f"-{suffix}"
            base_name = f"{timestamp}{counter}-{result.source.filename}"
            destinations = {
                extension: destination_dir / f"{base_name}{extension}"
                for extension in extensions
            }
            try:
                collision = any(path.exists() for path in destinations.values())
            except OSError as error:
                raise PersistenceError(
                    f"Could not inspect result directory '{destination_dir}': {error}."
                ) from error
            if collision:
                suffix += 1
                continue

            created: list[tuple[Path, FileIdentity]] = []
            try:
                for extension, path in destinations.items():
                    identity = _publish(contents[extension], path)
                    created.append((path, identity))
            except FileExistsError:
                try:
                    for path, identity in reversed(created):
                        _remove_created(path, identity)
                except OSError as error:
                    raise PersistenceError(
                        f"Could not clean up an interrupted save in "
                        f"'{destination_dir}': {error}."
                    ) from error
                suffix += 1
                continue
            except OSError as error:
                cleanup_error: OSError | None = None
                for path, identity in reversed(created):
                    try:
                        _remove_created(path, identity)
                    except OSError as caught:
                        cleanup_error = caught
                detail = cleanup_error or error
                raise PersistenceError(
                    f"Could not save result in '{destination_dir}': {detail}."
                ) from error

            return SavedResult(
                markdown=destinations.get(".md"),
                json=destinations.get(".json"),
            )
