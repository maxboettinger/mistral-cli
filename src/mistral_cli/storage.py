from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from mistral_cli import __version__
from mistral_cli.errors import PersistenceError
from mistral_cli.formatters import build_envelope, serialize_json
from mistral_cli.models import ApiResult, Operation, OutputFormat, SavedResult

FileIdentity = tuple[int, int]
_FALLBACK_NAME_MAX = 255
_MAX_PRESERVED_EXTENSION_BYTES = 16
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


@dataclass(frozen=True, slots=True)
class _PublishedFile:
    destination: Path
    source: Path
    identity: FileIdentity


class _LinuxRename(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(
        self,
        old_directory: int,
        old_path: bytes,
        new_directory: int,
        new_path: bytes,
        flags: int,
        /,
    ) -> int: ...


class _DarwinRename(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(
        self,
        old_path: bytes,
        new_path: bytes,
        flags: int,
        /,
    ) -> int: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_cli_version() -> str:
    return __version__


def _write_all(fd: int, content: bytes) -> None:
    with os.fdopen(fd, "wb", closefd=False) as output:
        output.write(content)
        output.flush()
        os.fsync(fd)


def _set_private_mode(fd: int, path: Path) -> None:
    if os.name != "posix":
        return
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(fd, 0o600)
    else:
        path.chmod(0o600)


def _publish(content: bytes, destination: Path) -> _PublishedFile:
    fd = -1
    temp_path: Path | None = None
    published = False
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".mistral-cli-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temp_path = Path(temp_name)
        _set_private_mode(fd, temp_path)
        _write_all(fd, content)
        stat_result = os.fstat(fd)
        identity = stat_result.st_dev, stat_result.st_ino
        os.close(fd)
        fd = -1
        os.link(temp_path, destination)
        result = _PublishedFile(destination, temp_path, identity)
        published = True
        return result
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None and not published:
            temp_path.unlink(missing_ok=True)


def _file_identity(path: Path) -> FileIdentity:
    stat_result = path.stat(follow_symlinks=False)
    return stat_result.st_dev, stat_result.st_ino


def _raise_rename_error(
    error_number: int,
    destination: Path,
) -> None:
    if error_number == errno.EEXIST:
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            destination,
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        destination,
    )


def _windows_rename_noreplace(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _linux_rename_noreplace(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = cast(_LinuxRename, libc.renameat2)
    except (AttributeError, OSError) as error:
        raise NotImplementedError(
            "atomic no-replace renameat2 is unavailable on this Linux system"
        ) from error

    argument_types: list[object] = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.argtypes = argument_types
    rename.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = rename(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        _raise_rename_error(ctypes.get_errno(), destination)


def _darwin_rename_noreplace(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = cast(_DarwinRename, libc.renamex_np)
    except (AttributeError, OSError) as error:
        raise NotImplementedError(
            "atomic no-replace renamex_np is unavailable on this macOS system"
        ) from error

    argument_types: list[object] = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.argtypes = argument_types
    rename.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = rename(
        os.fsencode(source),
        os.fsencode(destination),
        _RENAME_EXCL,
    )
    if result != 0:
        _raise_rename_error(ctypes.get_errno(), destination)


def _rename_noreplace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        _windows_rename_noreplace(source, destination)
    elif sys.platform.startswith("linux"):
        _linux_rename_noreplace(source, destination)
    elif sys.platform == "darwin":
        _darwin_rename_noreplace(source, destination)
    else:
        raise NotImplementedError(
            f"atomic no-replace move is unsupported on {sys.platform}"
        )


def _preservation_error(
    quarantine: Path,
    destination: Path,
    detail: object,
) -> OSError:
    return OSError(
        f"foreign rollback entry preserved at '{quarantine}' because "
        f"'{destination}' could not be safely reclaimed: {detail}"
    )


def _remove_restored_quarantine(
    quarantine: Path,
    destination: Path,
) -> None:
    try:
        quarantine.unlink()
    except OSError as error:
        raise _preservation_error(quarantine, destination, error) from error


def _restore_regular_file(quarantine: Path, destination: Path) -> None:
    try:
        os.link(quarantine, destination, follow_symlinks=False)
    except (OSError, TypeError, NotImplementedError) as error:
        raise _preservation_error(quarantine, destination, error) from error
    _remove_restored_quarantine(quarantine, destination)


def _restore_symlink(quarantine: Path, destination: Path) -> None:
    try:
        os.link(quarantine, destination, follow_symlinks=False)
    except (TypeError, NotImplementedError):
        try:
            target = os.readlink(quarantine)
            os.symlink(
                target,
                destination,
                target_is_directory=quarantine.is_dir(),
            )
        except (OSError, NotImplementedError) as error:
            raise _preservation_error(quarantine, destination, error) from error
    except OSError as error:
        raise _preservation_error(quarantine, destination, error) from error
    _remove_restored_quarantine(quarantine, destination)


def _restore_directory(
    quarantine: Path,
    destination: Path,
    stat_result: os.stat_result,
) -> None:
    mode = stat.S_IMODE(stat_result.st_mode)
    try:
        os.mkdir(destination, mode)
    except OSError as error:
        raise _preservation_error(quarantine, destination, error) from error

    try:
        for child in list(quarantine.iterdir()):
            _restore_foreign_entry(child, destination / child.name)
        os.chmod(destination, mode)
        os.utime(
            destination,
            ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns),
        )
        quarantine.rmdir()
    except OSError as error:
        raise _preservation_error(quarantine, destination, error) from error


def _restore_foreign_entry(quarantine: Path, destination: Path) -> None:
    try:
        stat_result = quarantine.stat(follow_symlinks=False)
    except OSError as error:
        raise _preservation_error(quarantine, destination, error) from error

    mode = stat_result.st_mode
    if stat.S_ISREG(mode):
        _restore_regular_file(quarantine, destination)
    elif stat.S_ISLNK(mode):
        _restore_symlink(quarantine, destination)
    elif stat.S_ISDIR(mode):
        _restore_directory(quarantine, destination, stat_result)
    else:
        raise _preservation_error(
            quarantine,
            destination,
            "entry type has no safe portable restoration primitive",
        )


def _remove_created(published: _PublishedFile) -> None:
    try:
        destination_identity = _file_identity(published.destination)
        source_identity = _file_identity(published.source)
    except FileNotFoundError:
        return
    if (
        destination_identity != published.identity
        or source_identity != published.identity
    ):
        return

    quarantine = Path(f"{published.source}.rollback")
    try:
        _rename_noreplace(published.destination, quarantine)
    except FileNotFoundError:
        return
    except (OSError, NotImplementedError) as error:
        raise OSError(
            f"rollback quarantine '{quarantine}' could not be reserved: {error}"
        ) from error
    if _file_identity(quarantine) != published.identity:
        _restore_foreign_entry(quarantine, published.destination)
        return
    quarantine.unlink()


def _rollback(published_files: list[_PublishedFile]) -> None:
    first_error: OSError | None = None
    for published in reversed(published_files):
        try:
            _remove_created(published)
        except OSError as error:
            first_error = first_error or error
    for published in published_files:
        try:
            published.source.unlink(missing_ok=True)
        except OSError as error:
            first_error = first_error or error
    if first_error is not None:
        raise first_error


def _discard_sources(published_files: list[_PublishedFile]) -> None:
    for published in published_files:
        published.source.unlink(missing_ok=True)


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
) -> tuple[str, dict[str, Path]]:
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
    return base_name, {
        extension: directory / f"{base_name}{extension}" for extension in extensions
    }


def _acquire_reservation(directory: Path, base_name: str) -> Path | None:
    digest = hashlib.sha256(base_name.encode("utf-8")).hexdigest()
    path = directory / f".mistral-cli-{digest}.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    os.close(fd)
    return path


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
        suffix = 0
        while True:
            try:
                base_name, destinations = _candidate_destinations(
                    directory=destination_dir,
                    timestamp=timestamp,
                    suffix=suffix,
                    source_filename=result.source.filename,
                    extensions=extensions,
                    name_max=name_max,
                )
                reservation = _acquire_reservation(
                    destination_dir,
                    base_name,
                )
            except OSError as error:
                raise PersistenceError(
                    f"Could not prepare result path in '{destination_dir}': {error}."
                ) from error
            if reservation is None:
                suffix += 1
                continue

            try:
                try:
                    collision = any(path.exists() for path in destinations.values())
                except OSError as error:
                    raise PersistenceError(
                        f"Could not inspect result directory "
                        f"'{destination_dir}': {error}."
                    ) from error
                if collision:
                    suffix += 1
                    continue

                created: list[_PublishedFile] = []
                try:
                    for extension, path in destinations.items():
                        created.append(_publish(contents[extension], path))
                except FileExistsError:
                    try:
                        _rollback(created)
                    except OSError as error:
                        raise PersistenceError(
                            f"Could not clean up an interrupted save in "
                            f"'{destination_dir}': {error}."
                        ) from error
                    suffix += 1
                    continue
                except OSError as error:
                    detail = error
                    try:
                        _rollback(created)
                    except OSError as cleanup_error:
                        detail = cleanup_error
                    raise PersistenceError(
                        f"Could not save result in '{destination_dir}': {detail}."
                    ) from error

                try:
                    _discard_sources(created)
                except OSError as error:
                    raise PersistenceError(
                        f"Could not clean up temporary result files in "
                        f"'{destination_dir}': {error}."
                    ) from error
                return SavedResult(
                    markdown=destinations.get(".md"),
                    json=destinations.get(".json"),
                )
            finally:
                try:
                    reservation.unlink(missing_ok=True)
                except OSError as error:
                    raise PersistenceError(
                        f"Could not release result reservation in "
                        f"'{destination_dir}': {error}."
                    ) from error
