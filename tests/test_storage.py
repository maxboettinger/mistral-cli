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
        assert saved.markdown.stat().st_mode & 0o777 == 0o600
    if saved.json is not None:
        assert saved.json.parent == output_dir
        envelope = json.loads(saved.json.read_text(encoding="utf-8"))
        assert envelope["schema_version"] == 1
        assert envelope["cli_version"] == "test-version"
        assert envelope["response"]["pages"][0]["markdown"] == "Héllo 👋"
        assert saved.json.read_bytes().endswith(b"\n")
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


def test_rollback_never_deletes_replacement_after_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    target = output_dir / "20260702T123456.123456Z-input.pdf.md"
    real_link = os.link
    real_stat = Path.stat
    real_unlink = Path.unlink
    link_calls = 0
    publish_collision = False
    replacement_installed = False

    def racing_link(source: Path | str, destination: Path | str) -> None:
        nonlocal link_calls, publish_collision
        link_calls += 1
        if link_calls == 2:
            publish_collision = True
            raise FileExistsError(destination)
        real_link(source, destination)

    def replacing_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement_installed
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == target and publish_collision and not replacement_installed:
            real_unlink(path)
            path.write_text("foreign replacement", encoding="utf-8")
            replacement_installed = True
        return result

    monkeypatch.setattr(storage.os, "link", racing_link)
    monkeypatch.setattr(Path, "stat", replacing_stat)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    assert replacement_installed
    assert target.read_text(encoding="utf-8") == "foreign replacement"
    assert saved.markdown == (output_dir / "20260702T123456.123456Z-1-input.pdf.md")
    assert all(
        not path.name.startswith(".mistral-cli-") for path in output_dir.iterdir()
    )


def test_rollback_restores_foreign_symlink_without_following_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    target = output_dir / "20260702T123456.123456Z-input.pdf.md"
    symlink_target = tmp_path / "foreign-target.txt"
    symlink_target.write_text("foreign target content", encoding="utf-8")
    real_link = os.link
    real_stat = Path.stat
    real_unlink = Path.unlink
    link_calls = 0
    publish_collision = False
    replacement_installed = False

    def racing_link(source: Path | str, destination: Path | str) -> None:
        nonlocal link_calls, publish_collision
        link_calls += 1
        if link_calls == 2:
            publish_collision = True
            raise FileExistsError(destination)
        real_link(source, destination)

    def replacing_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement_installed
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == target and publish_collision and not replacement_installed:
            real_unlink(path)
            path.symlink_to(symlink_target)
            replacement_installed = True
        return result

    monkeypatch.setattr(storage.os, "link", racing_link)
    monkeypatch.setattr(Path, "stat", replacing_stat)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    assert replacement_installed
    assert target.is_symlink()
    assert target.readlink() == symlink_target
    assert target.read_text(encoding="utf-8") == "foreign target content"
    assert saved.markdown == (output_dir / "20260702T123456.123456Z-1-input.pdf.md")
    assert all(".rollback" not in path.name for path in output_dir.iterdir())


def test_rollback_restores_foreign_directory_with_its_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    target = output_dir / "20260702T123456.123456Z-input.pdf.md"
    real_link = os.link
    real_stat = Path.stat
    real_unlink = Path.unlink
    link_calls = 0
    publish_collision = False
    replacement_installed = False

    def racing_link(source: Path | str, destination: Path | str) -> None:
        nonlocal link_calls, publish_collision
        link_calls += 1
        if link_calls == 2:
            publish_collision = True
            raise FileExistsError(destination)
        real_link(source, destination)

    def replacing_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement_installed
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == target and publish_collision and not replacement_installed:
            real_unlink(path)
            path.mkdir()
            (path / "foreign.txt").write_text(
                "foreign directory content",
                encoding="utf-8",
            )
            replacement_installed = True
        return result

    monkeypatch.setattr(storage.os, "link", racing_link)
    monkeypatch.setattr(Path, "stat", replacing_stat)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    saved = store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    assert replacement_installed
    assert target.is_dir()
    assert (target / "foreign.txt").read_text(encoding="utf-8") == (
        "foreign directory content"
    )
    assert saved.markdown == (output_dir / "20260702T123456.123456Z-1-input.pdf.md")
    assert all(".rollback" not in path.name for path in output_dir.iterdir())


def test_rollback_reports_quarantine_if_destination_becomes_occupied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    target = output_dir / "20260702T123456.123456Z-input.pdf.md"
    real_link = os.link
    real_stat = Path.stat
    real_unlink = Path.unlink
    real_lexists = os.path.lexists
    link_calls = 0
    publish_collision = False
    replacement_installed = False
    destination_reoccupied = False

    def racing_link(source: Path | str, destination: Path | str) -> None:
        nonlocal link_calls, publish_collision
        link_calls += 1
        if link_calls == 2:
            publish_collision = True
            raise FileExistsError(destination)
        real_link(source, destination)

    def replacing_stat(
        path: Path,
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement_installed
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == target and publish_collision and not replacement_installed:
            real_unlink(path)
            path.write_text("displaced foreign entry", encoding="utf-8")
            replacement_installed = True
        return result

    def occupying_lexists(path: str | os.PathLike[str]) -> bool:
        nonlocal destination_reoccupied
        if (
            Path(path) == target
            and replacement_installed
            and not destination_reoccupied
        ):
            target.write_text("new destination occupant", encoding="utf-8")
            destination_reoccupied = True
        return real_lexists(path)

    monkeypatch.setattr(storage.os, "link", racing_link)
    monkeypatch.setattr(Path, "stat", replacing_stat)
    monkeypatch.setattr(storage.os.path, "lexists", occupying_lexists)
    store = ResultStore(clock=fixed_clock, version=lambda: "v")

    with pytest.raises(
        PersistenceError,
        match="foreign rollback entry preserved",
    ) as raised:
        store.save(make_result(), "markdown", OutputFormat.BOTH, output_dir)

    quarantines = [
        path for path in output_dir.iterdir() if path.name.endswith(".rollback")
    ]
    assert destination_reoccupied
    assert target.read_text(encoding="utf-8") == "new destination occupant"
    assert len(quarantines) == 1
    assert quarantines[0].read_text(encoding="utf-8") == ("displaced foreign entry")
    assert str(quarantines[0]) in str(raised.value)


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
