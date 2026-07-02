from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mistral_cli.models import (
    InputSource,
    JSONMapping,
    OcrRequest,
    Operation,
    SourceKind,
    TranscriptionRequest,
)
from mistral_cli.services.ocr import OcrService
from mistral_cli.services.transcription import TranscriptionService


class FakeOcrGateway:
    def __init__(
        self,
        response: JSONMapping,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[OcrRequest] = []

    def ocr(self, request: OcrRequest) -> JSONMapping:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


class FakeTranscriptionGateway:
    def __init__(
        self,
        response: JSONMapping,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[TranscriptionRequest] = []

    def transcribe(self, request: TranscriptionRequest) -> JSONMapping:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def private_local_source() -> InputSource:
    return InputSource(
        kind=SourceKind.FILE,
        value="/private/customer/report.pdf",
        filename="report.pdf",
        path=Path("/private/customer/report.pdf"),
    )


def test_ocr_service_calls_gateway_once_and_builds_safe_complete_result() -> None:
    source = private_local_source()
    request = OcrRequest(
        source=source,
        model="mistral-ocr-latest",
        pages="0-2",
        table_format="html",
        extract_header=True,
        extract_footer=False,
        include_images=True,
        image_limit=0,
        image_min_size=12,
        include_blocks=True,
        confidence="page",
        timeout_ms=12_000,
    )
    response: JSONMapping = {
        "pages": [{"markdown": "result"}],
        "unknown": {"kept": True},
    }
    gateway = FakeOcrGateway(response)
    created_at = datetime(2026, 7, 2, 12, 30, tzinfo=UTC)
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return created_at

    result = OcrService(gateway, clock=clock).run(request)

    assert gateway.calls == [request]
    assert clock_calls == 1
    assert result.operation is Operation.OCR
    assert result.source is source
    assert result.response is response
    assert result.created_at is created_at
    assert result.request_metadata == {
        "model": "mistral-ocr-latest",
        "pages": "0-2",
        "table_format": "html",
        "extract_header": True,
        "extract_footer": False,
        "include_images": True,
        "image_limit": 0,
        "image_min_size": 12,
        "include_blocks": True,
        "confidence": "page",
        "timeout_ms": 12_000,
    }
    metadata_text = repr(result.request_metadata)
    assert source.value not in metadata_text
    assert "api_key" not in metadata_text
    assert "config" not in metadata_text


def test_transcription_service_calls_gateway_once_and_uses_json_lists() -> None:
    source = private_local_source()
    request = TranscriptionRequest(
        source=source,
        model="voxtral-mini-latest",
        language=None,
        temperature=0.0,
        diarize=True,
        context_bias=("Mistral", "Berlin"),
        timestamps=("segment", "word"),
        timeout_ms=30_000,
    )
    response: JSONMapping = {"text": "Transcript", "segments": []}
    gateway = FakeTranscriptionGateway(response)
    created_at = datetime(2026, 7, 2, 12, 31, tzinfo=UTC)
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return created_at

    result = TranscriptionService(gateway, clock=clock).run(request)

    assert gateway.calls == [request]
    assert clock_calls == 1
    assert result.operation is Operation.TRANSCRIPTION
    assert result.source is source
    assert result.response is response
    assert result.created_at is created_at
    assert result.request_metadata == {
        "model": "voxtral-mini-latest",
        "language": None,
        "temperature": 0.0,
        "diarize": True,
        "context_bias": ["Mistral", "Berlin"],
        "timestamps": ["segment", "word"],
        "timeout_ms": 30_000,
    }
    assert isinstance(result.request_metadata["context_bias"], list)
    assert isinstance(result.request_metadata["timestamps"], list)
    metadata_text = repr(result.request_metadata)
    assert source.value not in metadata_text
    assert "api_key" not in metadata_text
    assert "config" not in metadata_text


@pytest.mark.parametrize("operation", ["ocr", "transcription"])
def test_service_propagates_gateway_exception_without_calling_clock(
    operation: str,
) -> None:
    error = RuntimeError("SDK failed")
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return datetime.now(UTC)

    source = private_local_source()
    if operation == "ocr":
        gateway = FakeOcrGateway({}, error=error)
        calls = gateway.calls
        with pytest.raises(RuntimeError) as raised:
            OcrService(gateway, clock=clock).run(OcrRequest(source=source, model="ocr"))
    else:
        gateway_audio = FakeTranscriptionGateway({}, error=error)
        calls = gateway_audio.calls
        with pytest.raises(RuntimeError) as raised:
            TranscriptionService(gateway_audio, clock=clock).run(
                TranscriptionRequest(source=source, model="audio")
            )

    assert raised.value is error
    assert calls
    assert len(calls) == 1
    assert clock_calls == 0


@pytest.mark.parametrize("operation", ["ocr", "transcription"])
def test_service_leaves_naive_clock_rejection_to_api_result(operation: str) -> None:
    source = private_local_source()
    naive = datetime(2026, 7, 2, 12, 30)
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return naive

    if operation == "ocr":
        gateway = FakeOcrGateway({})
        calls = gateway.calls
        with pytest.raises(ValueError, match=r"created_at.*timezone-aware"):
            OcrService(gateway, clock=clock).run(OcrRequest(source=source, model="ocr"))
    else:
        gateway_audio = FakeTranscriptionGateway({})
        calls = gateway_audio.calls
        with pytest.raises(ValueError, match=r"created_at.*timezone-aware"):
            TranscriptionService(gateway_audio, clock=clock).run(
                TranscriptionRequest(source=source, model="audio")
            )

    assert len(calls) == 1
    assert clock_calls == 1
