from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mistral_cli.dedupe import (
    DedupeIndex,
    DedupeMatch,
    content_key,
    request_fingerprint,
)
from mistral_cli.errors import InputError
from mistral_cli.models import (
    InputSource,
    JSONValue,
    Operation,
    SavedResult,
    SourceKind,
)


@dataclass
class FakeClock:
    """A settable clock double, injected wherever `DedupeIndex` needs `now()`."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now


def make_file_source(path: Path, *, filename: str | None = None) -> InputSource:
    return InputSource(
        kind=SourceKind.FILE,
        value=str(path),
        filename=filename if filename is not None else path.name,
        path=path,
    )


def make_url_source(value: str) -> InputSource:
    return InputSource(kind=SourceKind.URL, value=value, filename="remote")


# --- content_key ------------------------------------------------------------


def test_content_key_hashes_local_file_bytes_with_sha256_prefix(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"hello world")

    expected = f"sha256:{hashlib.sha256(b'hello world').hexdigest()}"
    assert content_key(make_file_source(path)) == expected


def test_content_key_uses_url_prefix_with_the_raw_value() -> None:
    source = make_url_source("https://example.test/files/a.pdf?token=1")
    assert content_key(source) == "url:https://example.test/files/a.pdf?token=1"


def test_content_key_ignores_filename_for_identical_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "renamed-copy.pdf"
    first.write_bytes(b"identical payload")
    second.write_bytes(b"identical payload")

    assert content_key(make_file_source(first)) == content_key(make_file_source(second))


def test_content_key_differs_for_different_bytes_with_the_same_name(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.pdf"
    second = second_dir / "same.pdf"
    first.write_bytes(b"payload one")
    second.write_bytes(b"payload two")

    assert content_key(make_file_source(first)) != content_key(make_file_source(second))


def test_content_key_falls_back_to_the_value_path_when_path_is_unset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fallback.pdf"
    path.write_bytes(b"fallback bytes")
    without_path = InputSource(
        kind=SourceKind.FILE, value=str(path), filename="fallback.pdf"
    )

    assert content_key(without_path) == content_key(make_file_source(path))


def test_content_key_raises_input_error_for_an_unreadable_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    with pytest.raises(InputError, match="could not be hashed"):
        content_key(make_file_source(missing))


# --- request_fingerprint -----------------------------------------------------


def test_request_fingerprint_ignores_timeout_ms() -> None:
    base: dict[str, JSONValue] = {"model": "m", "language": "en", "timeout_ms": 1000}
    other_timeout = {**base, "timeout_ms": 999_999}

    assert request_fingerprint(base) == request_fingerprint(other_timeout)


def test_request_fingerprint_is_independent_of_key_order() -> None:
    a: dict[str, JSONValue] = {"model": "m", "language": "en", "diarize": False}
    b: dict[str, JSONValue] = {"diarize": False, "language": "en", "model": "m"}

    assert request_fingerprint(a) == request_fingerprint(b)


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "different-model"},
        {"diarize": True},
        {"language": "de"},
        {"pages": "0-2"},
        {"table_format": "html"},
    ],
    ids=["model", "diarize", "language", "pages", "table_format"],
)
def test_request_fingerprint_changes_when_request_metadata_changes(
    overrides: dict[str, JSONValue],
) -> None:
    base: dict[str, JSONValue] = {
        "model": "voxtral-mini-latest",
        "language": "en",
        "diarize": False,
        "pages": None,
        "table_format": None,
        "timeout_ms": 300_000,
    }
    changed = {**base, **overrides}

    assert request_fingerprint(base) != request_fingerprint(changed)


def test_request_fingerprint_sorts_timestamps_so_flag_order_does_not_matter() -> None:
    forward: dict[str, JSONValue] = {"model": "m", "timestamps": ["word", "segment"]}
    reverse: dict[str, JSONValue] = {"model": "m", "timestamps": ["segment", "word"]}

    assert request_fingerprint(forward) == request_fingerprint(reverse)


def test_request_fingerprint_changes_when_timestamps_granularity_differs() -> None:
    word_only: dict[str, JSONValue] = {"model": "m", "timestamps": ["word"]}
    both: dict[str, JSONValue] = {"model": "m", "timestamps": ["word", "segment"]}

    assert request_fingerprint(word_only) != request_fingerprint(both)


# --- DedupeIndex.lookup / record ---------------------------------------------


def test_lookup_returns_none_when_the_index_file_does_not_exist(tmp_path: Path) -> None:
    index = DedupeIndex(tmp_path / "missing" / "index.ndjson")

    match = index.lookup(
        operation=Operation.OCR,
        content_key="sha256:anything",
        request_fingerprint="fingerprint",
        window_days=30.0,
        require_markdown=False,
        require_json=False,
    )

    assert match is None


def test_record_then_lookup_round_trips_saved_artifacts(tmp_path: Path) -> None:
    saved_at = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    index = DedupeIndex(tmp_path / "index.ndjson", clock=FakeClock(saved_at))
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("# hi", encoding="utf-8")
    json_path = tmp_path / "result.json"
    json_path.write_text("{}", encoding="utf-8")

    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "mistral-ocr-latest"})
    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="mistral-ocr-latest",
        saved=SavedResult(markdown=markdown, json=json_path),
    )

    match = index.lookup(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        window_days=30.0,
        require_markdown=True,
        require_json=True,
    )

    assert match == DedupeMatch(
        saved_at=saved_at,
        markdown=markdown.resolve(),
        json=json_path.resolve(),
        model="mistral-ocr-latest",
    )


def test_record_is_a_noop_when_no_artifact_was_saved(tmp_path: Path) -> None:
    index_path = tmp_path / "index.ndjson"
    index = DedupeIndex(index_path, clock=FakeClock(datetime(2026, 7, 1, tzinfo=UTC)))
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")

    index.record(
        operation=Operation.OCR,
        content_key=content_key(make_file_source(source_path)),
        request_fingerprint=request_fingerprint({"model": "m"}),
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=None, json=None),
    )

    assert not index_path.exists()


@pytest.mark.parametrize(
    ("operation", "key_suffix", "fingerprint_suffix", "expect_match"),
    [
        pytest.param(Operation.TRANSCRIPTION, "", "", True, id="exact-identity-match"),
        pytest.param(Operation.OCR, "", "", False, id="different-operation"),
        pytest.param(
            Operation.TRANSCRIPTION, "-x", "", False, id="different-content-key"
        ),
        pytest.param(
            Operation.TRANSCRIPTION, "", "-x", False, id="different-fingerprint"
        ),
    ],
)
def test_lookup_requires_operation_content_key_and_fingerprint_to_all_match(
    tmp_path: Path,
    operation: Operation,
    key_suffix: str,
    fingerprint_suffix: str,
    expect_match: bool,
) -> None:
    index = DedupeIndex(
        tmp_path / "index.ndjson", clock=FakeClock(datetime(2026, 7, 1, tzinfo=UTC))
    )
    source_path = tmp_path / "input.mp3"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("hi", encoding="utf-8")
    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "voxtral-mini-latest"})
    index.record(
        operation=Operation.TRANSCRIPTION,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="voxtral-mini-latest",
        saved=SavedResult(markdown=markdown),
    )

    match = index.lookup(
        operation=operation,
        content_key=key + key_suffix,
        request_fingerprint=fingerprint + fingerprint_suffix,
        window_days=30.0,
        require_markdown=False,
        require_json=False,
    )

    assert (match is not None) is expect_match


def test_lookup_window_boundary_is_inclusive_of_the_exact_edge(tmp_path: Path) -> None:
    recorded_at = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FakeClock(recorded_at)
    index = DedupeIndex(tmp_path / "index.ndjson", clock=clock)
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("hi", encoding="utf-8")
    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "m"})
    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=markdown),
    )

    def lookup_at(now: datetime) -> DedupeMatch | None:
        clock.now = now
        return index.lookup(
            operation=Operation.OCR,
            content_key=key,
            request_fingerprint=fingerprint,
            window_days=10.0,
            require_markdown=False,
            require_json=False,
        )

    assert lookup_at(recorded_at + timedelta(days=10)) is not None
    assert lookup_at(recorded_at + timedelta(days=10, seconds=1)) is None


def test_lookup_clamps_an_extreme_window_and_still_matches_a_fresh_entry(
    tmp_path: Path,
) -> None:
    recorded_at = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FakeClock(recorded_at)
    index = DedupeIndex(tmp_path / "index.ndjson", clock=clock)
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("hi", encoding="utf-8")
    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "m"})
    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=markdown),
    )

    def lookup_with(window_days: float) -> DedupeMatch | None:
        return index.lookup(
            operation=Operation.OCR,
            content_key=key,
            request_fingerprint=fingerprint,
            window_days=window_days,
            require_markdown=False,
            require_json=False,
        )

    # Sanity check: once the entry is well outside a normal window it is
    # excluded -- this is what gives the huge-window assertion below teeth.
    clock.now = recorded_at + timedelta(days=40)
    assert lookup_with(30.0) is None

    # `--dedupe-window 1e12` must clamp instead of raising OverflowError out
    # of `timedelta(days=...)`, and should still find the (now 40-day-old)
    # entry -- i.e. behave like "almost forever" rather than crashing or
    # excluding everything.
    match = lookup_with(1e12)
    assert match is not None
    assert match.saved_at == recorded_at


def test_lookup_returns_the_most_recent_qualifying_entry(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 6, 1, tzinfo=UTC))
    index = DedupeIndex(tmp_path / "index.ndjson", clock=clock)
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "m"})
    older_markdown = tmp_path / "older.md"
    older_markdown.write_text("older", encoding="utf-8")
    newer_markdown = tmp_path / "newer.md"
    newer_markdown.write_text("newer", encoding="utf-8")

    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=older_markdown),
    )
    clock.now = datetime(2026, 6, 15, tzinfo=UTC)
    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=newer_markdown),
    )

    match = index.lookup(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        window_days=60.0,
        require_markdown=True,
        require_json=False,
    )

    assert match is not None
    assert match.saved_at == datetime(2026, 6, 15, tzinfo=UTC)
    assert match.markdown == newer_markdown.resolve()


def test_lookup_disqualifies_a_match_whose_required_artifact_was_deleted(
    tmp_path: Path,
) -> None:
    index = DedupeIndex(
        tmp_path / "index.ndjson", clock=FakeClock(datetime(2026, 7, 1, tzinfo=UTC))
    )
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("hi", encoding="utf-8")
    json_path = tmp_path / "result.json"
    json_path.write_text("{}", encoding="utf-8")
    key = content_key(make_file_source(source_path))
    fingerprint = request_fingerprint({"model": "m"})
    index.record(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=markdown, json=json_path),
    )

    def lookup(*, require_markdown: bool, require_json: bool) -> DedupeMatch | None:
        return index.lookup(
            operation=Operation.OCR,
            content_key=key,
            request_fingerprint=fingerprint,
            window_days=30.0,
            require_markdown=require_markdown,
            require_json=require_json,
        )

    assert lookup(require_markdown=True, require_json=True) is not None

    markdown.unlink()
    assert lookup(require_markdown=True, require_json=False) is None
    assert lookup(require_markdown=False, require_json=True) is not None
    assert lookup(require_markdown=False, require_json=False) is not None

    json_path.unlink()
    assert lookup(require_markdown=False, require_json=True) is None
    assert lookup(require_markdown=False, require_json=False) is not None


def test_lookup_ignores_corrupt_unknown_schema_and_non_dict_lines(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "index.ndjson"
    key = "sha256:abc123"
    fingerprint = "fingerprint-1"
    valid_entry: dict[str, JSONValue] = {
        "schema_version": 1,
        "operation": "ocr",
        "content_key": key,
        "request_fingerprint": fingerprint,
        "source": {"kind": "file", "value": "/tmp/a.pdf", "filename": "a.pdf"},
        "model": "mistral-ocr-latest",
        "saved_at": "2026-07-01T00:00:00Z",
        "artifacts": {"markdown": None, "json": None},
    }
    lines = [
        "",
        "not-json-at-all",
        json.dumps([1, 2, 3]),
        json.dumps({**valid_entry, "schema_version": 2}),
        json.dumps(valid_entry),
    ]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    index = DedupeIndex(index_path, clock=FakeClock(datetime(2026, 7, 2, tzinfo=UTC)))

    match = index.lookup(
        operation=Operation.OCR,
        content_key=key,
        request_fingerprint=fingerprint,
        window_days=30.0,
        require_markdown=False,
        require_json=False,
    )

    assert match is not None
    assert match.model == "mistral-ocr-latest"


def test_record_creates_the_index_file_with_private_permissions(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "nested" / "index.ndjson"
    index = DedupeIndex(index_path, clock=FakeClock(datetime(2026, 7, 1, tzinfo=UTC)))
    source_path = tmp_path / "input.pdf"
    source_path.write_bytes(b"payload")
    markdown = tmp_path / "result.md"
    markdown.write_text("hi", encoding="utf-8")

    index.record(
        operation=Operation.OCR,
        content_key=content_key(make_file_source(source_path)),
        request_fingerprint=request_fingerprint({"model": "m"}),
        source=make_file_source(source_path),
        model="m",
        saved=SavedResult(markdown=markdown),
    )

    if os.name == "posix":
        assert index_path.stat().st_mode & 0o777 == 0o600
