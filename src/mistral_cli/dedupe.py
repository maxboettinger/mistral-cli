from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from mistral_cli.errors import InputError
from mistral_cli.models import (
    InputSource,
    JSONMapping,
    JSONValue,
    Operation,
    SavedResult,
    SourceKind,
)
from mistral_cli.storage import utc_now

DEDUPE_INDEX_FILENAME = "index.ndjson"

_CHUNK_BYTES = 1024 * 1024
_INDEX_SCHEMA_VERSION = 1
# timedelta(days=...) overflows past this; clamping keeps huge windows ≈ forever.
_MAX_WINDOW_DAYS = 999_999_999


@dataclass(frozen=True, slots=True)
class DedupeMatch:
    """A prior saved result that satisfies an identity match."""

    saved_at: datetime
    markdown: Path | None
    json: Path | None
    model: str | None


def content_key(source: InputSource) -> str:
    """Return the identity key for a source's content."""
    if source.kind is SourceKind.URL:
        return f"url:{source.value}"

    path = source.path if source.path is not None else Path(source.value)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        raise InputError(f"source could not be hashed: {path}: {error}") from error
    return f"sha256:{digest.hexdigest()}"


def request_fingerprint(metadata: JSONMapping) -> str:
    """Return the identity key for a request's metadata, ignoring timeout_ms."""
    payload = dict(metadata)
    payload.pop("timeout_ms", None)
    # Timestamp granularities are order-insensitive; canonicalize them so CLI
    # flag order never defeats deduplication.
    timestamps = payload.get("timestamps")
    if isinstance(timestamps, list):
        strings = [item for item in timestamps if isinstance(item, str)]
        if len(strings) == len(timestamps):
            payload["timestamps"] = cast("list[JSONValue]", sorted(strings))
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_saved_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("saved_at must be a string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.utcoffset() is None:
        raise ValueError("saved_at must be timezone-aware")
    return parsed


def _artifact_path(value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("artifact path must be a string or null")
    return Path(value)


def _path_exists(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.exists()
    except OSError:
        return False


def _parse_entry(
    line: str,
    *,
    operation: Operation,
    content_key: str,
    request_fingerprint: str,
    require_markdown: bool,
    require_json: bool,
) -> DedupeMatch | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        raw = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    entry = cast("dict[str, object]", raw)

    try:
        if entry.get("schema_version") != _INDEX_SCHEMA_VERSION:
            return None
        if entry["operation"] != operation.value:
            return None
        if entry["content_key"] != content_key:
            return None
        if entry["request_fingerprint"] != request_fingerprint:
            return None
        saved_at = _parse_saved_at(entry["saved_at"])
        artifacts = entry["artifacts"]
        if not isinstance(artifacts, dict):
            return None
        artifacts = cast("dict[str, object]", artifacts)
        markdown_path = _artifact_path(artifacts["markdown"])
        json_path = _artifact_path(artifacts["json"])
        model = entry["model"]
        if model is not None and not isinstance(model, str):
            return None
    except (ValueError, TypeError, KeyError):
        return None

    if require_markdown and not _path_exists(markdown_path):
        return None
    if require_json and not _path_exists(json_path):
        return None

    return DedupeMatch(
        saved_at=saved_at, markdown=markdown_path, json=json_path, model=model
    )


def _saved_at_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("saved_at must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _resolved_path_text(path: Path | None) -> str | None:
    return None if path is None else str(path.resolve())


class DedupeIndex:
    """Append-only NDJSON index of saved results, used to skip duplicates."""

    def __init__(self, path: Path, clock: Callable[[], datetime] = utc_now) -> None:
        self._path = path
        self._clock = clock

    def lookup(
        self,
        *,
        operation: Operation,
        content_key: str,
        request_fingerprint: str,
        window_days: float,
        require_markdown: bool,
        require_json: bool,
    ) -> DedupeMatch | None:
        """Return the most recent still-valid entry matching the given identity."""
        try:
            handle = self._path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return None

        best: DedupeMatch | None = None
        now = self._clock()
        window = timedelta(days=min(window_days, _MAX_WINDOW_DAYS))
        with handle:
            for line in handle:
                candidate = _parse_entry(
                    line,
                    operation=operation,
                    content_key=content_key,
                    request_fingerprint=request_fingerprint,
                    require_markdown=require_markdown,
                    require_json=require_json,
                )
                if candidate is None:
                    continue
                if now - candidate.saved_at > window:
                    continue
                if best is None or candidate.saved_at > best.saved_at:
                    best = candidate
        return best

    def record(
        self,
        *,
        operation: Operation,
        content_key: str,
        request_fingerprint: str,
        source: InputSource,
        model: str | None,
        saved: SavedResult,
    ) -> None:
        """Append one entry describing a newly saved result."""
        if saved.markdown is None and saved.json is None:
            return

        entry: dict[str, JSONValue] = {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "operation": operation.value,
            "content_key": content_key,
            "request_fingerprint": request_fingerprint,
            "source": {
                "kind": source.kind.value,
                "value": source.value,
                "filename": source.filename,
            },
            "model": model,
            "saved_at": _saved_at_text(self._clock()),
            "artifacts": {
                "markdown": _resolved_path_text(saved.markdown),
                "json": _resolved_path_text(saved.json),
            },
        }
        payload = (
            json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
