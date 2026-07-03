from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mistral_cli.models import (
    ApiResult,
    JSONMapping,
    Operation,
    TranscriptionRequest,
    transcription_request_metadata,
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
        return ApiResult(
            operation=Operation.TRANSCRIPTION,
            source=request.source,
            request_metadata=transcription_request_metadata(request),
            response=response,
            created_at=self._clock(),
        )
