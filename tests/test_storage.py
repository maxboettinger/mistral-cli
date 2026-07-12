import json
import os
from datetime import UTC, datetime, timedelta, timezone
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
    filename: str = "input.pdf",
) -> ApiResult:
    return ApiResult(
        operation=operation,
        source=InputSource(
            kind=SourceKind.FILE,
            value=f"/tmp/{filename}",
            filename=filename,
            path=Path("/tmp") / filename,
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


def test_non_utc_clock_is_normalized_in_filename(tmp_path: Path) -> None:
    def non_utc_clock() -> datetime:
        return datetime(
            2026,
            7,
            2,
            23,
            34,
            56,
            123456,
            tzinfo=timezone(timedelta(hours=-7)),
        )

    store = ResultStore(clock=non_utc_clock, version=lambda: "v")

    saved = store.save(
        make_result(),
        "markdown",
        OutputFormat.MD,
        tmp_path,
    )

    assert saved.markdown == tmp_path / "20260703T063456.123456Z-input.pdf.md"


def test_unicode_sanitized_filename_is_preserved_in_names_and_content(
    tmp_path: Path,
) -> None:
    filename = "über 文.pdf"
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(
        make_result(filename=filename),
        "Grüße 文\n",
        OutputFormat.BOTH,
        tmp_path,
    )

    assert saved.markdown == tmp_path / f"20260702T123456.123456Z-{filename}.md"
    assert saved.json == tmp_path / f"20260702T123456.123456Z-{filename}.json"
    assert saved.markdown is not None
    assert saved.markdown.read_text(encoding="utf-8") == "Grüße 文\n"
    assert saved.json is not None
    envelope = json.loads(saved.json.read_text(encoding="utf-8"))
    assert envelope["source"]["filename"] == filename


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
        if os.name == "posix":
            assert saved.markdown.stat().st_mode & 0o777 == 0o600
    if saved.json is not None:
        assert saved.json.parent == output_dir
        envelope = json.loads(saved.json.read_text(encoding="utf-8"))
        assert envelope["schema_version"] == 1
        assert envelope["cli_version"] == "test-version"
        assert envelope["response"]["pages"][0]["markdown"] == "Héllo 👋"
        assert saved.json.read_bytes().endswith(b"\n")
        if os.name == "posix":
            assert saved.json.stat().st_mode & 0o777 == 0o600


def test_save_works_when_fchmod_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(storage.os, "fchmod", raising=False)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.MD, tmp_path)

    assert saved.markdown is not None
    assert saved.markdown.read_text(encoding="utf-8") == "markdown"
    if os.name == "posix":
        assert saved.markdown.stat().st_mode & 0o777 == 0o600


def test_private_mode_skips_fchmod_on_non_posix_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "temporary"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)

    def unexpected_fchmod(fd: int, mode: int) -> None:
        del fd, mode
        raise AssertionError("fchmod must not be called on non-POSIX systems")

    monkeypatch.setattr(storage.os, "fchmod", unexpected_fchmod, raising=False)
    monkeypatch.setattr(storage.os, "name", "nt")
    try:
        storage._set_private_mode(  # pyright: ignore[reportPrivateUsage]
            fd,
            path,
        )
    finally:
        os.close(fd)


def test_long_ascii_filename_and_collision_suffix_fit_name_max(
    tmp_path: Path,
) -> None:
    filename = f"{'a' * 230}.pdf"
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    first = store.save(
        make_result(filename=filename),
        "first",
        OutputFormat.BOTH,
        tmp_path,
    )
    second = store.save(
        make_result(filename=filename),
        "second",
        OutputFormat.BOTH,
        tmp_path,
    )

    for saved in (first, second):
        assert saved.markdown is not None
        assert saved.json is not None
        assert len(saved.markdown.name.encode("utf-8")) <= 255
        assert len(saved.json.name.encode("utf-8")) <= 255
        assert saved.markdown.name.removesuffix(".md").endswith(".pdf")
        assert saved.json.name.removesuffix(".json").endswith(".pdf")
    assert second.markdown is not None
    assert "Z-1-" in second.markdown.name


def test_long_multibyte_filename_is_truncated_on_codepoint_boundary(
    tmp_path: Path,
) -> None:
    filename = f"{'文' * 100}.pdf"
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(
        make_result(filename=filename),
        "unicode",
        OutputFormat.BOTH,
        tmp_path,
    )

    assert saved.markdown is not None
    assert saved.json is not None
    assert len(saved.markdown.name.encode("utf-8")) <= 255
    assert len(saved.json.name.encode("utf-8")) <= 255
    assert saved.markdown.name.removesuffix(".md").endswith(".pdf")
    assert saved.json.name.removesuffix(".json").endswith(".pdf")
    assert "文" in saved.markdown.name


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


def test_link_failure_is_translated_and_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_link(source: str, destination: str) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(storage.os, "link", failing_link)
    store = ResultStore(base_dir=tmp_path)

    with pytest.raises(PersistenceError, match="Could not save result"):
        store.save(make_result(), "# md\n", OutputFormat.BOTH)

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_write_failure_is_translated_and_temp_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fsync(fd: int) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(storage.os, "fsync", failing_fsync)
    store = ResultStore(base_dir=tmp_path)

    with pytest.raises(PersistenceError, match="Could not save result"):
        store.save(make_result(), "# md\n", OutputFormat.MD)

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_race_on_first_name_falls_back_to_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link
    calls = {"count": 0}

    def racing_link(source: str, destination: str) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            # Another process claims the destination between candidate
            # generation and our link.
            Path(destination).write_text("winner", encoding="utf-8")
        real_link(source, destination)

    monkeypatch.setattr(storage.os, "link", racing_link)
    store = ResultStore(base_dir=tmp_path)

    saved = store.save(make_result(), "# md\n", OutputFormat.MD)

    assert saved.markdown is not None
    assert "-1-" in saved.markdown.name
    assert saved.markdown.read_text(encoding="utf-8") == "# md\n"


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
