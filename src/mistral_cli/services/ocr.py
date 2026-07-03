from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mistral_cli.models import (
    ApiResult,
    JSONMapping,
    OcrRequest,
    Operation,
    ocr_request_metadata,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OcrGateway(Protocol):
    def ocr(self, request: OcrRequest) -> JSONMapping: ...


class OcrService:
    def __init__(
        self,
        gateway: OcrGateway,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._gateway = gateway
        self._clock = clock

    def run(self, request: OcrRequest) -> ApiResult:
        response = self._gateway.ocr(request)
        return ApiResult(
            operation=Operation.OCR,
            source=request.source,
            request_metadata=ocr_request_metadata(request),
            response=response,
            created_at=self._clock(),
        )
