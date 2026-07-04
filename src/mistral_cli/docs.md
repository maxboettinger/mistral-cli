# Noridoc: mistral_cli

Path: @/src/mistral_cli

### Overview

The complete implementation of `mistral-cli`, a Python 3.11+ command-line tool
that wraps the Mistral API for two use cases — OCR (document/image text
extraction) and audio transcription. It is distributed as the `mistral` console
command whose entry point is `mistral_cli.cli.main:cli` (see
[`pyproject.toml`](../../pyproject.toml)). `__main__.py` provides the same entry
via `python -m mistral_cli`, and `__init__.py` exposes `__version__` resolved
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
 mistral_client.py  ── the ONLY importer of `mistralai`
          │  returns plain-JSON mappings
          ▼
     ApiResult ──▶ formatters.py ──▶ storage.py (durable .md/.json)
                                          │
                                          ▼
                                    dedupe.py (records the save in
                                    ~/.mistral/index.ndjson)

 supporting: models.py (types + validation), sources.py (input resolution),
             config.py (TOML + key resolution), errors.py + console.py (safe I/O)
```

Command modules in [`cli/`](cli/docs.md) orchestrate the flow; [`services/`](services/docs.md)
sit behind gateway protocols; `mistral_client.py` is the adapter. This isolation
is what lets the entire test suite run without live API calls by substituting
fake gateways.

### Core Implementation

Everything the internal code passes around is defined in
[`models.py`](models.py): the frozen `InputSource`, `OcrRequest`,
`TranscriptionRequest`, and the pivotal `ApiResult`/`SavedResult`, none of which
depend on Click or the SDK. The `build_ocr_request` / `build_transcription_request`
factories are the single validation gate — normalizing page ranges, enforcing the
`--language`/`--timestamps` incompatibility, de-duplicating timestamp
granularities, bounding context-bias, and converting `--timeout` seconds to the
SDK's `timeout_ms`.

`sources.py` resolves each raw argument into an `InputSource`: Windows drive
paths and scheme-less values become local files (validated as readable regular
files); `http`/`https` values become URLs; other schemes are rejected. It also
sanitizes the derived filename and decides the OCR variant (`image_url` vs
`document_url`) from MIME type or URL suffix.

`mistral_client.MistralGateway` maps a request to SDK kwargs, opens the client
per call, and — critically — converts the SDK response into a strict plain-JSON
mapping via `model_dump(mode="json", exclude_unset=True)` plus recursive
validation, so no SDK object ever escapes this module. Local OCR files are
base64 data URLs; local transcription files are streamed as open binary handles
with their filename; URL sources use the SDK's `file_url`.

`formatters.py` deterministically renders `ApiResult` to Markdown (provenance
header + per-page or per-segment content) and builds the stable JSON envelope
(`schema_version`, `created_at`, `source`, `request`, `response`, `cli_version`),
omitting internal/secret-like request keys. `storage.py` writes those bytes
atomically and collision-safely with UTC-timestamped filenames.

`dedupe.py` gives each request a content-addressed identity: `content_key()`
hashes a local file's bytes (`sha256:<hex>`) or keys a URL source by its
literal value, and `request_fingerprint()` hashes the same
`*_request_metadata()` used for the JSON envelope (minus `timeout_ms`, with
`timestamps` order-normalized so flag order never defeats a match).
`DedupeIndex.lookup()` / `.record()` read and append the append-only
`~/.mistral/index.ndjson` that the batch runner in [`cli/`](cli/docs.md) uses
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
- **Atomic, non-clobbering persistence**: `storage.py` reserves a per-name lock
  file, publishes each output through `mkstemp` + `os.link`, and uses
  platform-specific no-replace renames (`renameat2` on Linux, `renamex_np` on
  macOS) with a foreign-entry rollback path so an interrupted multi-file save
  never leaves partial or overwritten results; on collision it retries with an
  incrementing `-N` suffix.
- **Best-effort duplicate index**: `dedupe.py`'s `~/.mistral/index.ndjson` is
  append-only (`O_APPEND` + `fsync`, mode `0600`); a missing file means no
  duplicates, corrupt or unrecognized lines are ignored on read, and any
  `OSError` on lookup or record degrades to a stderr warning instead of
  failing the run. `--no-save` runs never write to it.
- **Error translation** in `errors.py` maps SDK/network/HTTP failures to concise
  domain messages (`ApiError`, `ConfigError`, `InputError`, `PersistenceError`)
  without leaking untrusted exception text, unless `--debug` is set.

Created and maintained by Nori.
