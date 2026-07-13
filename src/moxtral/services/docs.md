# Noridoc: services

Path: @/src/moxtral/services

### Overview

The use-case layer for the two operations the CLI supports: OCR and audio
transcription. Each service orchestrates a single API call and wraps the raw
response in an internal [`ApiResult`](../models.py), the immutable value that
flows onward to formatting and storage.

### How it fits into the larger codebase

Services sit between the Click command layer ([`cli/`](../cli/docs.md)) and the
SDK adapter ([`mistral_client.py`](../mistral_client.py)). They depend only on a
narrow *gateway protocol* (`OcrGateway.ocr` / `TranscriptionGateway.transcribe`),
never on `mistralai` directly, so the command layer can inject the real
[`MistralGateway`](../mistral_client.py) in production or a fake gateway in tests.
This is the boundary that keeps the dependency direction pointing inward: commands
depend on services, services depend on a protocol, and only the adapter imports
the SDK. The CLI constructs a service by calling `OcrService(create_gateway(...))`
or `TranscriptionService(create_gateway(...))` in [`cli/ocr.py`](../cli/ocr.py)
and [`cli/transcribe.py`](../cli/transcribe.py).

### Core Implementation

Each service exposes a single `run(request)` method. It calls the gateway to get
a plain-JSON response mapping, then assembles a `request_metadata` dictionary of
the effective non-secret options (model, timeout_ms, and operation-specific
flags) and returns an `ApiResult` stamping `created_at` from an injectable
`clock` (defaults to `datetime.now(UTC)`). The `request_metadata` keys are what
later populate the JSON envelope's `request` object; secret/path-like keys are
stripped downstream in [`formatters.py`](../formatters.py), not here.

### Things to Know

The `clock` parameter exists so tests can pin `created_at` deterministically;
`ApiResult.__post_init__` rejects any naive (timezone-unaware) datetime, so the
UTC clock is a hard invariant. The services perform no validation, source
resolution, or persistence — request objects arrive already validated by the
`build_*_request` factories in [`models.py`](../models.py), and the returned
`ApiResult` is handed back to the command for redaction, formatting, and saving.

Created and maintained by Nori.
