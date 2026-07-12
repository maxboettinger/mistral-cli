from __future__ import annotations

import inspect
from collections.abc import Mapping
from contextlib import AbstractContextManager
from io import BufferedReader
from pathlib import Path
from types import TracebackType
from typing import cast

import pytest
from mistralai.client.utils.retries import RetryConfig

from mistral_cli.mistral_client import MistralGateway
from mistral_cli.models import (
    InputSource,
    OcrRequest,
    OcrSourceKind,
    SourceKind,
    TranscriptionRequest,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.dump_calls: list[dict[str, object]] = []

    def model_dump(self, **kwargs: object) -> object:
        self.dump_calls.append(kwargs)
        return self.payload


class FakeOcrEndpoint:
    def __init__(
        self,
        response: FakeResponse,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def process(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeTranscriptionEndpoint:
    def __init__(
        self,
        response: FakeResponse,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.upload_content: bytes | None = None
        self.upload_stream: object | None = None

    def complete(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        upload = kwargs.get("file")
        if isinstance(upload, Mapping):
            stream = cast(BufferedReader, upload["content"])
            self.upload_stream = stream
            self.upload_content = stream.read()
        if self.error is not None:
            raise self.error
        return self.response


class FakeAudio:
    def __init__(self, transcriptions: FakeTranscriptionEndpoint) -> None:
        self.transcriptions = transcriptions


class FakeClient(AbstractContextManager["FakeClient"]):
    def __init__(
        self,
        *,
        response_payload: object,
        ocr_error: Exception | None = None,
        transcription_error: Exception | None = None,
    ) -> None:
        response = FakeResponse(response_payload)
        self.response = response
        self.ocr = FakeOcrEndpoint(response, ocr_error)
        self.audio = FakeAudio(
            FakeTranscriptionEndpoint(response, transcription_error),
        )
        self.entered = False
        self.exited = False

    def __enter__(self) -> FakeClient:
        self.entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True


class FakeFactory:
    def __init__(self, clients: list[FakeClient]) -> None:
        self.clients = clients
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeClient:
        self.calls.append(kwargs)
        return self.clients[len(self.calls) - 1]


def local_source(
    path: Path,
    *,
    ocr_kind: OcrSourceKind = OcrSourceKind.DOCUMENT,
) -> InputSource:
    return InputSource(
        kind=SourceKind.FILE,
        value=str(path),
        filename=path.name,
        path=path,
        ocr_kind=ocr_kind,
    )


def url_source(
    url: str,
    *,
    filename: str,
    ocr_kind: OcrSourceKind = OcrSourceKind.DOCUMENT,
) -> InputSource:
    return InputSource(
        kind=SourceKind.URL,
        value=url,
        filename=filename,
        ocr_kind=ocr_kind,
    )


def test_ocr_local_document_maps_all_options_and_converts_response(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")
    payload = {
        "pages": [{"index": 0, "markdown": "# Report"}],
        "unknown": {"items": (1, "two")},
    }
    client = FakeClient(response_payload=payload)
    factory = FakeFactory([client])
    gateway = MistralGateway(api_key="top-secret", client_factory=factory)
    request = OcrRequest(
        source=local_source(path),
        model="mistral-ocr-latest",
        pages="0,2-4",
        table_format="markdown",
        extract_header=True,
        extract_footer=True,
        include_images=True,
        image_limit=7,
        image_min_size=0,
        include_blocks=True,
        confidence="word",
        retries=0,
        timeout_ms=42_000,
    )

    result = gateway.ocr(request)

    assert client.ocr.calls == [
        {
            "model": "mistral-ocr-latest",
            "document": {
                "type": "document_url",
                "document_url": "data:application/pdf;base64,cGRm",
            },
            "timeout_ms": 42_000,
            "pages": "0,2-4",
            "table_format": "markdown",
            "extract_header": True,
            "extract_footer": True,
            "include_image_base64": True,
            "image_limit": 7,
            "image_min_size": 0,
            "include_blocks": True,
            "confidence_scores_granularity": "word",
        }
    ]
    assert result == {
        "pages": [{"index": 0, "markdown": "# Report"}],
        "unknown": {"items": [1, "two"]},
    }
    assert type(result) is dict
    assert type(cast(dict[str, object], result)["unknown"]) is dict
    assert client.response.dump_calls == [
        {"mode": "json", "exclude_unset": True},
    ]
    assert factory.calls == [{"api_key": "top-secret"}]
    assert client.entered
    assert client.exited
    assert "top-secret" not in repr(gateway)
    assert not hasattr(gateway, "api_key")


def test_ocr_local_image_uses_image_data_url(tmp_path: Path) -> None:
    path = tmp_path / "scan.png"
    path.write_bytes(b"png")
    client = FakeClient(response_payload={"pages": []})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.ocr(
        OcrRequest(
            source=local_source(path, ocr_kind=OcrSourceKind.IMAGE),
            model="ocr",
            retries=0,
            timeout_ms=1,
        )
    )

    assert client.ocr.calls == [
        {
            "model": "ocr",
            "document": {
                "type": "image_url",
                "image_url": "data:image/png;base64,cG5n",
            },
            "timeout_ms": 1,
        }
    ]


@pytest.mark.parametrize(
    ("ocr_kind", "expected_document"),
    [
        (
            OcrSourceKind.DOCUMENT,
            {
                "type": "document_url",
                "document_url": "https://example.test/report.pdf",
            },
        ),
        (
            OcrSourceKind.IMAGE,
            {
                "type": "image_url",
                "image_url": "https://example.test/report.pdf",
            },
        ),
    ],
)
def test_ocr_url_uses_original_url_and_omits_unset_options(
    ocr_kind: OcrSourceKind,
    expected_document: dict[str, str],
) -> None:
    client = FakeClient(response_payload={"pages": []})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))
    request = OcrRequest(
        source=url_source(
            "https://example.test/report.pdf",
            filename="report.pdf",
            ocr_kind=ocr_kind,
        ),
        model="ocr",
        retries=0,
        timeout_ms=300_000,
    )

    gateway.ocr(request)

    assert client.ocr.calls == [
        {
            "model": "ocr",
            "document": expected_document,
            "timeout_ms": 300_000,
        }
    ]


def test_ocr_preserves_zero_image_controls(tmp_path: Path) -> None:
    path = tmp_path / "report.bin"
    path.write_bytes(b"contents")
    client = FakeClient(response_payload={"pages": []})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.ocr(
        OcrRequest(
            source=local_source(path),
            model="ocr",
            include_images=True,
            image_limit=0,
            image_min_size=0,
        )
    )

    assert client.ocr.calls[0]["image_limit"] == 0
    assert client.ocr.calls[0]["image_min_size"] == 0
    assert client.ocr.calls[0]["include_image_base64"] is True


def test_local_transcription_stream_is_open_only_during_sdk_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "interview.wav"
    path.write_bytes(b"audio bytes")
    client = FakeClient(response_payload={"text": "hello"})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    result = gateway.transcribe(
        TranscriptionRequest(
            source=local_source(path),
            model="voxtral-mini-latest",
            retries=0,
            timeout_ms=8_000,
        )
    )

    endpoint = client.audio.transcriptions
    call = endpoint.calls[0]
    upload = cast(Mapping[str, object], call["file"])
    assert set(upload) == {"content", "file_name"}
    assert upload["file_name"] == "interview.wav"
    assert endpoint.upload_content == b"audio bytes"
    assert endpoint.upload_stream is upload["content"]
    assert cast(BufferedReader, upload["content"]).closed
    assert call == {
        "model": "voxtral-mini-latest",
        "file": upload,
        "timeout_ms": 8_000,
    }
    assert result == {"text": "hello"}
    assert client.exited


def test_url_transcription_maps_all_options_including_zero_temperature() -> None:
    client = FakeClient(response_payload={"text": "hello", "extra": {"value": 1}})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))
    request = TranscriptionRequest(
        source=url_source(
            "https://example.test/interview.mp3?signature=secret",
            filename="interview.mp3",
        ),
        model="voxtral",
        language="de",
        temperature=0.0,
        diarize=True,
        context_bias=("Mistral", "Berlin"),
        timestamps=("segment", "word"),
        retries=0,
        timeout_ms=9_000,
    )

    result = gateway.transcribe(request)

    assert client.audio.transcriptions.calls == [
        {
            "model": "voxtral",
            "file_url": "https://example.test/interview.mp3?signature=secret",
            "language": "de",
            "temperature": 0.0,
            "diarize": True,
            "context_bias": ["Mistral", "Berlin"],
            "timestamp_granularities": ["segment", "word"],
            "timeout_ms": 9_000,
        }
    ]
    assert result == {"text": "hello", "extra": {"value": 1}}


def test_transcription_omits_false_empty_and_unset_options() -> None:
    client = FakeClient(response_payload={"text": ""})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.transcribe(
        TranscriptionRequest(
            source=url_source(
                "https://example.test/audio.wav",
                filename="audio.wav",
            ),
            model="voxtral",
            language=None,
            temperature=None,
            diarize=False,
            context_bias=(),
            timestamps=(),
            retries=0,
            timeout_ms=2,
        )
    )

    assert client.audio.transcriptions.calls == [
        {
            "model": "voxtral",
            "file_url": "https://example.test/audio.wav",
            "timeout_ms": 2,
        }
    ]


@pytest.mark.parametrize("operation", ["ocr", "transcription"])
def test_sdk_exceptions_propagate_unchanged_and_client_closes(
    tmp_path: Path,
    operation: str,
) -> None:
    error = RuntimeError("network failed")
    client = FakeClient(
        response_payload={},
        ocr_error=error if operation == "ocr" else None,
        transcription_error=error if operation == "transcription" else None,
    )
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))
    path = tmp_path / "input.bin"
    path.write_bytes(b"input")

    with pytest.raises(RuntimeError) as raised:
        if operation == "ocr":
            gateway.ocr(OcrRequest(source=local_source(path), model="ocr"))
        else:
            gateway.transcribe(
                TranscriptionRequest(source=local_source(path), model="audio")
            )

    assert raised.value is error
    assert client.exited
    if operation == "transcription":
        stream = client.audio.transcriptions.upload_stream
        assert stream is not None
        assert cast(BufferedReader, stream).closed


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "mapping"],
        {"bad": object()},
        {1: "non-string key"},
    ],
)
def test_unexpected_sdk_response_shape_raises_actionable_type_error(
    payload: object,
) -> None:
    client = FakeClient(response_payload=payload)
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    with pytest.raises(TypeError, match=r"SDK response.*JSON mapping"):
        gateway.ocr(
            OcrRequest(
                source=url_source(
                    "https://example.test/report.pdf",
                    filename="report.pdf",
                ),
                model="ocr",
            )
        )

    assert client.exited


def test_installed_sdk_signatures_cover_adapter_contract() -> None:
    from mistralai.client import Mistral

    with Mistral(api_key="contract-test-only") as client:
        ocr_parameters = inspect.signature(client.ocr.process).parameters
        transcription_parameters = inspect.signature(
            client.audio.transcriptions.complete
        ).parameters

    assert {
        "model",
        "document",
        "pages",
        "include_image_base64",
        "image_limit",
        "image_min_size",
        "table_format",
        "extract_header",
        "extract_footer",
        "include_blocks",
        "confidence_scores_granularity",
        "timeout_ms",
    } <= ocr_parameters.keys()
    assert {
        "model",
        "file",
        "file_url",
        "language",
        "temperature",
        "diarize",
        "context_bias",
        "timestamp_granularities",
        "timeout_ms",
    } <= transcription_parameters.keys()


def test_ocr_passes_backoff_retry_config_for_positive_retries() -> None:
    client = FakeClient(response_payload={"pages": []})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.ocr(
        OcrRequest(
            source=url_source(
                "https://example.test/report.pdf",
                filename="report.pdf",
            ),
            model="ocr",
            retries=3,
        )
    )

    (call,) = client.ocr.calls
    config = call["retries"]
    assert isinstance(config, RetryConfig)
    assert config.strategy == "backoff"
    assert config.retry_connection_errors is True
    # 1s + 2s + 4s of backoff plus 1s jitter allowance per retry = 10s budget
    assert config.backoff.max_elapsed_time == 10_000


def test_ocr_omits_retry_config_when_retries_is_zero() -> None:
    client = FakeClient(response_payload={"pages": []})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.ocr(
        OcrRequest(
            source=url_source(
                "https://example.test/report.pdf",
                filename="report.pdf",
            ),
            model="ocr",
            retries=0,
        )
    )

    (call,) = client.ocr.calls
    assert "retries" not in call


def test_transcribe_passes_backoff_retry_config_for_positive_retries() -> None:
    client = FakeClient(response_payload={"text": "hello"})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.transcribe(
        TranscriptionRequest(
            source=url_source(
                "https://example.test/interview.mp3",
                filename="interview.mp3",
            ),
            model="voxtral",
            retries=3,
        )
    )

    (call,) = client.audio.transcriptions.calls
    config = call["retries"]
    assert isinstance(config, RetryConfig)
    assert config.strategy == "backoff"
    assert config.retry_connection_errors is True
    assert config.backoff.max_elapsed_time == 10_000


def test_transcribe_omits_retry_config_when_retries_is_zero() -> None:
    client = FakeClient(response_payload={"text": "hello"})
    gateway = MistralGateway("key", client_factory=FakeFactory([client]))

    gateway.transcribe(
        TranscriptionRequest(
            source=url_source(
                "https://example.test/interview.mp3",
                filename="interview.mp3",
            ),
            model="voxtral",
            retries=0,
        )
    )

    (call,) = client.audio.transcriptions.calls
    assert "retries" not in call
