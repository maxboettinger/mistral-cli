from pathlib import Path
from unittest.mock import patch

import pytest

from moxtral.errors import InputError, MistralCliError
from moxtral.models import OcrSourceKind, Operation, SourceKind
from moxtral.sources import resolve_source


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
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

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
        "http://localhost:8080/report.pdf",
        "http://[::1]:8080/report.pdf",
    ],
)
def test_urls_with_valid_ports_and_ipv6_are_accepted(url: str) -> None:
    assert resolve_source(url, "document").value == url


@pytest.mark.parametrize(
    "url",
    [
        "https:///tmp/file.pdf",
        "http:foo.pdf",
        "http://:8080/file.pdf",
    ],
)
def test_http_urls_without_a_hostname_are_rejected(url: str) -> None:
    with pytest.raises(InputError, match=r"source.*URL.*hostname"):
        resolve_source(url, "document")


def test_malformed_ipv6_url_is_translated_to_input_error() -> None:
    with pytest.raises(InputError, match=r"source.*URL"):
        resolve_source("http://[::1", "document")


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


@pytest.mark.parametrize("value", [r"C:\tmp\file.pdf", "C:/tmp/file.pdf"])
def test_windows_drive_path_is_treated_as_local(value: str) -> None:
    with (
        patch.object(Path, "exists", return_value=False),
        pytest.raises(InputError, match=r"source path does not exist") as error,
    ):
        resolve_source(value, "document")

    assert "scheme" not in str(error.value)


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


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("report%3Ffinal.pdf", "report_final.pdf"),
        (
            "bad%3C%3E%3A%22%7C%3F%2Aname.pdf",
            "bad_______name.pdf",
        ),
        ("report.pdf.%20", "report.pdf"),
    ],
)
def test_url_filename_is_portable_across_platforms(
    component: str,
    expected: str,
) -> None:
    source = resolve_source(f"https://example.test/{component}", "document")

    assert source.filename == expected


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("CON.pdf", "_CON.pdf"),
        ("aux", "_aux"),
        ("lPt9.txt", "_lPt9.txt"),
    ],
)
def test_windows_reserved_url_name_is_prefixed(
    component: str,
    expected: str,
) -> None:
    assert (
        resolve_source(f"https://example.test/{component}", "document").filename
        == expected
    )


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


def _file_of_size(tmp_path: Path, name: str, size: int) -> Path:
    path = tmp_path / name
    with path.open("wb") as handle:
        if size:
            handle.seek(size - 1)
            handle.write(b"\x00")
    return path


def test_local_ocr_source_over_50mb_is_rejected(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "big.pdf", limit + 1)

    with pytest.raises(InputError, match="50 MB"):
        resolve_source(str(path), Operation.OCR)


def test_local_ocr_source_at_exactly_50mb_is_accepted(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "edge.pdf", limit)

    source = resolve_source(str(path), Operation.OCR)

    assert source.filename == "edge.pdf"


def test_oversized_audio_source_is_not_size_limited(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "long.mp3", limit + 1)

    source = resolve_source(str(path), Operation.TRANSCRIPTION)

    assert source.filename == "long.mp3"
