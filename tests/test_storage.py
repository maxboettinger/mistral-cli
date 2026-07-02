import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mistral_cli import storage
from mistral_cli.errors import PersistenceError
from mistral_cli.models import (
    ApiResult,
    InputSource,
    JSONMapping,
    Operation,
    OutputFormat,
    SourceKind,
)
from mistral_cli.storage import ResultStore


def fixed_clock() -> datetime:
    return datetime(2026, 7, 2, 12, 34, 56, 123456, tzinfo=UTC)


def make_result(
    operation: Operation = Operation.OCR,
    *,
    response: JSONMapping | None = None,
) -> ApiResult:
    return ApiResult(
        operation=operation,
        source=InputSource(
            kind=SourceKind.FILE,
            value="/tmp/input.pdf",
            filename="input.pdf",
            path=Path("/tmp/input.pdf"),
        ),
        request_metadata={"model": "model-v1"},
        response=(
            response
            if response is not None
            else {"pages": [{"index": 0, "markdown": "Héllo 👋"}]}
        ),
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
    )


def test_default_directories_and_clock_called_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return fixed_clock()

    def fake_expanduser(self: Path) -> Path:
        return tmp_path / "home"

    monkeypatch.setattr(Path, "expanduser", fake_expanduser)
    store = ResultStore(clock=clock, version=lambda: "test-version")

    ocr_saved = store.save(make_result(), "ocr", OutputFormat.MD)
    transcription_saved = store.save(
        make_result(Operation.TRANSCRIPTION, response={"text": "audio"}),
        "audio",
        OutputFormat.MD,
    )

    assert ocr_saved.markdown == (
        tmp_path / "home/ocr/20260702T123456.123456Z-input.pdf.md"
    )
    assert transcription_saved.markdown == (
        tmp_path / "home/transcriptions/20260702T123456.123456Z-input.pdf.md"
    )
    assert calls == 2


@pytest.mark.parametrize(
    ("output_format", "markdown_exists", "json_exists"),
    [
        (OutputFormat.MD, True, False),
        (OutputFormat.JSON, False, True),
        (OutputFormat.BOTH, True, True),
    ],
)
def test_save_honors_format_and_exact_custom_directory(
    tmp_path: Path,
    output_format: OutputFormat,
    markdown_exists: bool,
    json_exists: bool,
) -> None:
    output_dir = tmp_path / "exact"
    store = ResultStore(
        base_dir=tmp_path / "ignored",
        clock=fixed_clock,
        version=lambda: "test-version",
    )

    saved = store.save(make_result(), "Grüße 👋\n", output_format, output_dir)

    assert (saved.markdown is not None) is markdown_exists
    assert (saved.json is not None) is json_exists
    if saved.markdown is not None:
        assert saved.markdown.parent == output_dir
        assert saved.markdown.read_text(encoding="utf-8") == "Grüße 👋\n"
        assert saved.markdown.stat().st_mode & 0o777 == 0o600
    if saved.json is not None:
        assert saved.json.parent == output_dir
        envelope = json.loads(saved.json.read_text(encoding="utf-8"))
        assert envelope["schema_version"] == 1
        assert envelope["cli_version"] == "test-version"
        assert envelope["response"]["pages"][0]["markdown"] == "Héllo 👋"
        assert saved.json.read_bytes().endswith(b"\n")
        assert saved.json.stat().st_mode & 0o777 == 0o600


def test_collision_suffixes_cover_the_entire_requested_set(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    first_json = output_dir / "20260702T123456.123456Z-input.pdf.json"
    first_json.write_text("preexisting", encoding="utf-8")
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    assert saved.markdown == (output_dir / "20260702T123456.123456Z-1-input.pdf.md")
    assert saved.json == output_dir / "20260702T123456.123456Z-1-input.pdf.json"
    assert first_json.read_text(encoding="utf-8") == "preexisting"
    assert not (output_dir / "20260702T123456.123456Z-input.pdf.md").exists()


def test_publish_race_rolls_back_only_this_attempt_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    real_link = os.link
    link_calls = 0

    def racing_link(source: Path | str, destination: Path | str) -> None:
        nonlocal link_calls
        link_calls += 1
        destination_path = Path(destination)
        if link_calls == 2:
            destination_path.write_text("racer", encoding="utf-8")
            raise FileExistsError(destination_path)
        real_link(source, destination)

    monkeypatch.setattr(storage.os, "link", racing_link)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    base = "20260702T123456.123456Z-input.pdf"
    assert not (output_dir / f"{base}.md").exists()
    assert (output_dir / f"{base}.json").read_text(encoding="utf-8") == "racer"
    assert saved.markdown == output_dir / "20260702T123456.123456Z-1-input.pdf.md"
    assert saved.json == output_dir / "20260702T123456.123456Z-1-input.pdf.json"


def test_link_failure_leaves_no_partial_or_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"

    def unsupported_link(source: Path | str, destination: Path | str) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(storage.os, "link", unsupported_link)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    with pytest.raises(PersistenceError, match="save"):
        store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    assert list(output_dir.iterdir()) == []


def test_write_failure_is_translated_and_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"

    def failing_write(fd: int, content: bytes) -> None:
        del fd, content
        raise OSError("disk full")

    monkeypatch.setattr(storage, "_write_all", failing_write)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    with pytest.raises(PersistenceError, match="save"):
        store.save(make_result(), "markdown", OutputFormat.MD, output_dir)

    assert list(output_dir.iterdir()) == []


def test_collision_check_failure_is_translated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    real_exists = Path.exists

    def failing_exists(path: Path) -> bool:
        if path.parent == output_dir:
            raise OSError("cannot inspect directory")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", failing_exists)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    with pytest.raises(PersistenceError, match="inspect"):
        store.save(make_result(), "markdown", OutputFormat.MD, output_dir)


def test_serialization_failure_occurs_before_any_file_is_published(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    result = make_result(response={"value": float("nan")})
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    with pytest.raises(PersistenceError, match="JSON"):
        store.save(result, "markdown", OutputFormat.BOTH, output_dir)

    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_markdown_only_does_not_require_json_serialization(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    result = make_result(response={"value": float("nan")})
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(result, "markdown", OutputFormat.MD, output_dir)

    assert saved.markdown is not None
    assert saved.markdown.read_text(encoding="utf-8") == "markdown"
    assert saved.json is None


def test_naive_clock_is_rejected_without_writing(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"

    def naive_clock() -> datetime:
        return datetime(2026, 7, 2)

    store = ResultStore(clock=naive_clock, version=lambda: "v")

    with pytest.raises(PersistenceError, match="timezone-aware"):
        store.save(make_result(), "markdown", OutputFormat.MD, output_dir)

    assert not output_dir.exists()


def test_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    original = output_dir / "20260702T123456.123456Z-input.pdf.md"
    original.write_text("original", encoding="utf-8")
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "new", OutputFormat.MD, output_dir)

    assert original.read_text(encoding="utf-8") == "original"
    assert saved.markdown == output_dir / "20260702T123456.123456Z-1-input.pdf.md"
    assert saved.markdown is not None
    assert saved.markdown.read_text(encoding="utf-8") == "new"
