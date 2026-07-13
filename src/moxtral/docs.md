# Noridoc: moxtral

Path: @/src/moxtral

### Overview

The complete implementation of `moxtral`, a Python 3.11+ command-line tool
that wraps the Mistral API for two use cases — OCR (document/image text
extraction) and audio transcription. It is distributed as the `moxtral` console
command whose entry point is `moxtral.cli.main:cli` (see
[`pyproject.toml`](../../pyproject.toml)). `__main__.py` provides the same entry
via `python -m moxtral`, and `__init__.py` exposes `__version__` resolved
from installed package metadata.

### How it fits into the larger codebase

The package is a layered hexagonal-style design where dependencies point inward
and only one module touches the vendor SDK:

```
        cli/ (Click commands, redaction, console)
          │  builds request objects, injects gateways;
          │  consults dedupe.py before this pipeline runs
          ▼
   services/ (OcrService / TranscriptionService)
          │  call a narrow gateway Protocol
          ▼
 moxtralent.py  ── the ONLY importer of `mistralai`
          │  returns plain-JSON mappings
          ▼
     ApiResult ──▶ formatters.py ──▶ storage.py (durable .md/.json)
                                          │
                                          ▼
                                    dedupe.py (records the save in
                                    ~/.moxtral/index.ndjson)

 supporting: models.py (types + validation), sources.py (input resolution),
             config.py (TOML + key resolution), errors.py + console.py (safe I/O)
```

Command modules in [`cli/`](cli/docs.md) orchestrate the flow; [`services/`](services/docs.md)
sit behind gateway protocols; `moxtralent.py` is the adapter. This isolation
is what lets the entire test suite run without live API calls by substituting
fake gateways.

### Core Implementation

Everything the internal code passes around is defined in
[`models.py`](models.py): the frozen `InputSource`, `OcrRequest`,
`TranscriptionRequest`, and the pivotal `ApiResult`/`SavedResult`, none of which
depend on Click or the SDK. The `build_ocr_request` / `build_transcription_request`
factories are the single validation gate — normalizing page ranges, enforcing the
`--language`/`--timestamps` incompatibility, de-duplicating timestamp
granularities, bounding context-bias, converting `--timeout` seconds to the
SDK's `timeout_ms`, and rejecting a negative `retries` (default `DEFAULT_RETRIES
= 3`). `retries` is echoed into `ocr_request_metadata()`/
`transcription_request_metadata()` alongside the other saved options.

`sources.py` resolves each raw argument into an `InputSource`: Windows drive
paths and scheme-less values become local files (validated as readable regular
files); `http`/`https` values become URLs; other schemes are rejected. It also
sanitizes the derived filename and decides the OCR variant (`image_url` vs
`document_url`) from MIME type or URL suffix.

`moxtralent.MistralGateway` maps a request to SDK kwargs, opens the client
per call, and — critically — converts the SDK response into a strict plain-JSON
mapping via `model_dump(mode="json", exclude_unset=True)` plus recursive
validation, so no SDK object ever escapes this module. Local OCR files are
base64 data URLs; local transcription files are streamed as open binary handles
with their filename; URL sources use the SDK's `file_url`. It is also the only
place that maps the domain `retries` count onto the SDK's retry behavior:
`_retry_config()` converts an attempt count into the SDK's time-budgeted
exponential-backoff `RetryConfig`/`BackoffStrategy` (there's no attempt-count
knob, only a `max_elapsed_time` budget), returning `None` — and omitting the
`retries` kwarg entirely — when the request's `retries` is `0`. Retries cover
HTTP 429/5xx and connection/timeout failures and only add latency: a call that
never succeeds is never billed.

`formatters.py` deterministically renders `ApiResult` to Markdown (provenance
header + per-page or per-segment content) and builds the stable JSON envelope
(`schema_version`, `created_at`, `source`, `request`, `response`, `cli_version`),
omitting internal/secret-like request keys. `storage.py` writes those bytes
atomically and collision-safely with UTC-timestamped filenames.

`dedupe.py` gives each request a content-addressed identity: `content_key()`
hashes a local file's bytes (`sha256:<hex>`) or keys a URL source by its
literal value, and `request_fingerprint()` hashes the same
`*_request_metadata()` used for the JSON envelope (minus `timeout_ms` and
`retries`, with `timestamps` order-normalized so flag order never defeats a
match) — retry count is an execution detail, not part of a request's identity.
`DedupeIndex.lookup()` / `.record()` read and append the append-only
`~/.moxtral/index.ndjson` that the batch runner in [`cli/`](cli/docs.md) uses
to skip duplicates and then record new results.

### Things to Know

Several invariants are enforced structurally across this package:

- **Timezone-aware timestamps**: `ApiResult.__post_init__` rejects naive
  datetimes, and both the service clock and storage clock produce UTC. Filenames
  use `YYYYMMDDTHHMMSS.ffffffZ-<source filename>`.
- **Secret handling** is defense-in-depth: keys are never accepted as CLI
  arguments (only hidden prompt, `--stdin`, or `MISTRAL_API_KEY`, with env taking
  precedence over stored config in [`config.py`](config.py)); results are scrubbed
  by `redact_result` before formatting/saving; and every terminal write passes
  through `safe_terminal_text` which both strips ANSI/control sequences
  ([`console.py`](console.py)) and redacts known secrets ([`errors.py`](errors.py)),
  including debug tracebacks.
- **Config safety**: `config.py` creates directories `0700`, writes the file
  `0600` through a same-directory temp file + atomic `os.replace`, parses TOML
  strictly, and preserves unrelated keys on update. The key is still stored as
  plaintext.
- **Atomic, non-clobbering persistence**: `storage.py` writes full content to a
  private temp file (`mkstemp`, `fsync`) in the destination directory, then
  `os.link`s it to each final name; `os.link` fails atomically on `FileExistsError`
  if the name is taken, so there's no separate existence check, lock file, or
  rename step. On collision it unlinks the sibling links/temp files created for
  that attempt and retries with an incrementing `-N` suffix. This gives the same
  two guarantees as before with less machinery: a file is never overwritten
  (enforced by `os.link`'s atomicity), and a visible file is always complete
  (enforced by writing + fsyncing before it's ever linked into a visible name).
- **Best-effort duplicate index**: `dedupe.py`'s `~/.moxtral/index.ndjson` is
  append-only (`O_APPEND` + `fsync`, mode `0600`); a missing file means no
  duplicates, corrupt or unrecognized lines are ignored on read, and any
  `OSError` on lookup or record degrades to a stderr warning instead of
  failing the run. `--no-save` runs never write to it.
- **Error translation** in `errors.py` maps SDK/network/HTTP failures to concise
  domain messages (`ApiError`, `ConfigError`, `InputError`, `PersistenceError`)
  without leaking untrusted exception text, unless `--debug` is set.
- **SDK types stay confined to `moxtralent.py`**: the `retries` field is a
  plain `int` everywhere else in the codebase (models, services, CLI); only
  `moxtralent.py` translates it into the SDK's `RetryConfig`/
  `BackoffStrategy`, matching the rule that `mistralai` is imported nowhere else.

Created and maintained by Nori.
