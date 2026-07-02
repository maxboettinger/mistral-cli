from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mistral_cli.models import (
    ApiResult,
    JSONMapping,
    JSONValue,
    Operation,
    TranscriptionRequest,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TranscriptionGateway(Protocol):
    def transcribe(self, request: TranscriptionRequest) -> JSONMapping: ...


class TranscriptionService:
    def __init__(
        self,
        gateway: TranscriptionGateway,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._gateway = gateway
        self._clock = clock

    def run(self, request: TranscriptionRequest) -> ApiResult:
        response = self._gateway.transcribe(request)
        metadata: dict[str, JSONValue] = {
            "model": request.model,
            "language": request.language,
            "temperature": request.temperature,
            "diarize": request.diarize,
            "context_bias": list(request.context_bias),
            "timestamps": list(request.timestamps),
            "timeout_ms": request.timeout_ms,
        }
        return ApiResult(
            operation=Operation.TRANSCRIPTION,
            source=request.source,
            request_metadata=metadata,
            response=response,
            created_at=self._clock(),
        )
