from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from mistral_cli.models import (
    ApiResult,
    JSONMapping,
    JSONValue,
    OcrRequest,
    Operation,
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
        metadata: dict[str, JSONValue] = {
            "model": request.model,
            "pages": request.pages,
            "table_format": request.table_format,
            "extract_header": request.extract_header,
            "extract_footer": request.extract_footer,
            "include_images": request.include_images,
            "image_limit": request.image_limit,
            "image_min_size": request.image_min_size,
            "include_blocks": request.include_blocks,
            "confidence": request.confidence,
            "timeout_ms": request.timeout_ms,
        }
        return ApiResult(
            operation=Operation.OCR,
            source=request.source,
            request_metadata=metadata,
            response=response,
            created_at=self._clock(),
        )
