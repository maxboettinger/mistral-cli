from __future__ import annotations

import base64
import math
import mimetypes
from collections.abc import Callable, Mapping
from io import BufferedReader
from types import TracebackType
from typing import Protocol, cast

from mistralai.client import Mistral
from mistralai.client.utils.retries import BackoffStrategy, RetryConfig

from moxtral.models import (
    InputSource,
    JSONMapping,
    JSONValue,
    OcrRequest,
    OcrSourceKind,
    SourceKind,
    TranscriptionRequest,
)


class _OcrEndpoint(Protocol):
    def process(self, **kwargs: object) -> object: ...


class _TranscriptionEndpoint(Protocol):
    def complete(self, **kwargs: object) -> object: ...


class _AudioEndpoint(Protocol):
    transcriptions: _TranscriptionEndpoint


class _SdkClient(Protocol):
    ocr: _OcrEndpoint
    audio: _AudioEndpoint

    def __enter__(self) -> _SdkClient: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object: ...


class _ClientFactory(Protocol):
    def __call__(self, *, api_key: str) -> object: ...


class _DumpableResponse(Protocol):
    def model_dump(self, *, mode: str, exclude_unset: bool) -> object: ...


def _sdk_response_mapping(response: object) -> dict[str, JSONValue]:
    model_dump = getattr(response, "model_dump", None)
    if not callable(model_dump):
        raise TypeError(
            "SDK response must support model_dump and produce a JSON mapping."
        )
    dumped = cast(_DumpableResponse, response).model_dump(
        mode="json",
        exclude_unset=True,
    )

    if not isinstance(dumped, Mapping):
        raise TypeError("SDK response must produce a JSON mapping.")
    return _json_mapping(cast(Mapping[object, object], dumped))


def _json_mapping(value: Mapping[object, object]) -> dict[str, JSONValue]:
    converted: dict[str, JSONValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(
                "SDK response must produce a JSON mapping with string keys."
            )
        converted[key] = _json_value(item)
    return converted


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise TypeError("SDK response must produce a JSON mapping with finite numbers.")
    if isinstance(value, Mapping):
        return _json_mapping(cast(Mapping[object, object], value))
    if isinstance(value, (list, tuple)):
        values = cast(list[object] | tuple[object, ...], value)
        return [_json_value(item) for item in values]
    raise TypeError(
        "SDK response must produce a JSON mapping containing only JSON values."
    )


_RETRY_INITIAL_INTERVAL_MS = 1_000
_RETRY_MAX_INTERVAL_MS = 10_000
_RETRY_EXPONENT = 2.0
_RETRY_JITTER_ALLOWANCE_MS = 1_000


def _retry_config(retries: int) -> RetryConfig | None:
    """Map a retry attempt count onto the SDK's time-budgeted backoff loop."""
    if retries <= 0:
        return None
    budget_ms = 0
    for attempt in range(retries):
        interval = _RETRY_INITIAL_INTERVAL_MS * _RETRY_EXPONENT**attempt
        if interval >= _RETRY_MAX_INTERVAL_MS:
            remaining = retries - attempt
            budget_ms += remaining * (
                _RETRY_MAX_INTERVAL_MS + _RETRY_JITTER_ALLOWANCE_MS
            )
            break
        budget_ms += int(interval) + _RETRY_JITTER_ALLOWANCE_MS
    return RetryConfig(
        strategy="backoff",
        backoff=BackoffStrategy(
            initial_interval=_RETRY_INITIAL_INTERVAL_MS,
            max_interval=_RETRY_MAX_INTERVAL_MS,
            exponent=_RETRY_EXPONENT,
            max_elapsed_time=budget_ms,
        ),
        retry_connection_errors=True,
    )


def _source_path(source: InputSource) -> BufferedReader:
    if source.kind is not SourceKind.FILE or source.path is None:
        raise ValueError("local source must include a filesystem path")
    return source.path.open("rb")


def _ocr_document(source: InputSource) -> dict[str, object]:
    location = source.value
    if source.kind is SourceKind.FILE:
        with _source_path(source) as stream:
            encoded = base64.b64encode(stream.read()).decode("ascii")
        mime_type = mimetypes.guess_type(source.filename)[0]
        if mime_type is None:
            mime_type = "application/octet-stream"
        location = f"data:{mime_type};base64,{encoded}"

    if source.ocr_kind is OcrSourceKind.IMAGE:
        return {"type": "image_url", "image_url": location}
    return {"type": "document_url", "document_url": location}


class MistralGateway:
    def __init__(
        self,
        api_key: str,
        client_factory: _ClientFactory | None = None,
    ) -> None:
        factory = (
            cast(_ClientFactory, Mistral) if client_factory is None else client_factory
        )
        self._new_client: Callable[[], _SdkClient] = lambda: cast(
            _SdkClient,
            factory(api_key=api_key),
        )

    def ocr(self, request: OcrRequest) -> JSONMapping:
        kwargs: dict[str, object] = {
            "model": request.model,
            "document": _ocr_document(request.source),
            "timeout_ms": request.timeout_ms,
        }
        if request.pages is not None:
            kwargs["pages"] = request.pages
        if request.table_format is not None:
            kwargs["table_format"] = request.table_format
        if request.extract_header:
            kwargs["extract_header"] = True
        if request.extract_footer:
            kwargs["extract_footer"] = True
        if request.include_images:
            kwargs["include_image_base64"] = True
        if request.image_limit is not None:
            kwargs["image_limit"] = request.image_limit
        if request.image_min_size is not None:
            kwargs["image_min_size"] = request.image_min_size
        if request.include_blocks:
            kwargs["include_blocks"] = True
        if request.confidence is not None:
            kwargs["confidence_scores_granularity"] = request.confidence
        retry_config = _retry_config(request.retries)
        if retry_config is not None:
            kwargs["retries"] = retry_config

        with self._new_client() as client:
            response = client.ocr.process(**kwargs)
            return _sdk_response_mapping(response)

    def transcribe(self, request: TranscriptionRequest) -> JSONMapping:
        kwargs: dict[str, object] = {
            "model": request.model,
            "timeout_ms": request.timeout_ms,
        }
        if request.language is not None:
            kwargs["language"] = request.language
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.diarize:
            kwargs["diarize"] = True
        if request.context_bias:
            kwargs["context_bias"] = list(request.context_bias)
        if request.timestamps:
            kwargs["timestamp_granularities"] = list(request.timestamps)
        retry_config = _retry_config(request.retries)
        if retry_config is not None:
            kwargs["retries"] = retry_config

        with self._new_client() as client:
            if request.source.kind is SourceKind.FILE:
                with _source_path(request.source) as stream:
                    kwargs["file"] = {
                        "content": stream,
                        "file_name": request.source.filename,
                    }
                    response = client.audio.transcriptions.complete(**kwargs)
            else:
                kwargs["file_url"] = request.source.value
                response = client.audio.transcriptions.complete(**kwargs)
            return _sdk_response_mapping(response)
