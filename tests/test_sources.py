from pathlib import Path
from unittest.mock import patch

import pytest

from mistral_cli.errors import InputError, MistralCliError
from mistral_cli.models import OcrSourceKind, Operation, SourceKind
from mistral_cli.sources import resolve_source


def test_readable_local_file_is_expanded_and_preserves_path(tmp_path: Path) -> None:
    path = tmp_path / "document.pdf"
    path.write_bytes(b"document")

    source = resolve_source(str(path), "document")

    assert source.kind is SourceKind.FILE
    assert source.value == str(path)
    assert source.filename == "document.pdf"
    assert source.path == path


def test_local_tilde_is_expanded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.pdf"
    path.write_bytes(b"document")
    monkeypatch.setenv("HOME", str(tmp_path))

    source = resolve_source("~/document.pdf", Operation.OCR)

    assert source.value == str(path)
    assert source.path == path


@pytest.mark.parametrize("value", ["missing.pdf", "nested/missing.pdf"])
def test_missing_local_file_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(InputError, match=r"source.*does not exist"):
        resolve_source(str(tmp_path / value), "document")


def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError, match=r"source.*regular file"):
        resolve_source(str(tmp_path), "document")


def test_file_that_cannot_be_opened_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "document.pdf"
    path.write_bytes(b"document")

    with (
        patch.object(Path, "open", side_effect=PermissionError("denied")),
        pytest.raises(InputError, match=r"source.*read.*denied"),
    ):
        resolve_source(str(path), "document")


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("http://example.test/report.pdf", SourceKind.URL),
        ("HTTPS://example.test/report.pdf", SourceKind.URL),
    ],
)
def test_http_and_https_urls_are_accepted(url: str, kind: SourceKind) -> None:
    source = resolve_source(url, "document")

    assert source.kind is kind
    assert source.value == url
    assert source.path is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/file.pdf",
        "file:///tmp/file.pdf",
        "gopher://example.test/file.pdf",
    ],
)
def test_unsupported_explicit_url_scheme_is_rejected(url: str) -> None:
    with pytest.raises(InputError, match=r"source.*http.*https"):
        resolve_source(url, "document")


def test_url_filename_is_decoded_and_query_is_ignored() -> None:
    source = resolve_source(
        "https://example.test/My%20File.pdf?token=x#ignored",
        "document",
    )

    assert source.filename == "My File.pdf"
    assert source.kind is SourceKind.URL


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("document", "remote-document"),
        ("audio", "remote-audio"),
        (Operation.OCR, "remote-document"),
        (Operation.TRANSCRIPTION, "remote-audio"),
    ],
)
def test_empty_url_path_uses_operation_fallback(
    purpose: str | Operation,
    expected: str,
) -> None:
    assert resolve_source("https://example.test/", purpose).filename == expected


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("bad%2Fname.pdf", "bad_name.pdf"),
        ("bad%5Cname.pdf", "bad_name.pdf"),
        ("bad%00%1F%7F%C2%80name.pdf", "bad____name.pdf"),
    ],
)
def test_url_filename_replaces_encoded_separators_and_controls(
    component: str,
    expected: str,
) -> None:
    source = resolve_source(f"https://example.test/{component}", "document")

    assert source.filename == expected
    assert "\x00" not in source.filename


@pytest.mark.parametrize("component", [".", "..", "%2E", "%2E%2E"])
def test_dot_only_url_name_uses_fallback(component: str) -> None:
    source = resolve_source(f"https://example.test/{component}", "document")

    assert source.filename == "remote-document"


def test_url_filename_preserves_unicode_and_extension() -> None:
    source = resolve_source(
        "https://example.test/%E6%8A%A5%E5%91%8A.final.PDF",
        "document",
    )

    assert source.filename == "报告.final.PDF"


def test_ocr_image_url_is_classified_from_suffix() -> None:
    source = resolve_source("https://example.test/scan.PNG", "document")

    assert source.ocr_kind is OcrSourceKind.IMAGE


def test_local_image_is_classified_from_mimetype(tmp_path: Path) -> None:
    path = tmp_path / "scan.jpeg"
    path.write_bytes(b"image")

    assert resolve_source(str(path), "document").ocr_kind is OcrSourceKind.IMAGE


def test_local_unknown_extension_defaults_to_document(tmp_path: Path) -> None:
    path = tmp_path / "input.unknown"
    path.write_bytes(b"document")

    assert resolve_source(str(path), "document").ocr_kind is OcrSourceKind.DOCUMENT


def test_input_error_is_an_expected_cli_error() -> None:
    assert issubclass(InputError, MistralCliError)
