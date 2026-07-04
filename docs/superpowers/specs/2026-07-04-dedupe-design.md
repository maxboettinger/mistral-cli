# Duplicate-result skipping (dedupe) — design

Date: 2026-07-04. Status: approved for implementation.

## Problem

Re-running `mistral transcribe`/`mistral ocr` on a source that already has a
saved result re-bills the API. The CLI should skip sources whose identical,
recent result already exists on disk — without ever skipping work that would
produce a *different* result, and with an explicit override.

## Identity model (what counts as "the same work")

A prior result is a duplicate of the current request iff ALL match:

1. **Operation** — `ocr` and `transcription` never dedupe against each other.
2. **Content key** —
   - local file: `sha256:<hex>` over the file *bytes* (filenames are
     irrelevant: renamed copies still dedupe; same-named different files never do);
   - URL: `url:<resolved source value>` (remote bytes are not fetchable
     without paying; the recency window bounds staleness).
3. **Request fingerprint** — `sha256` over the canonical JSON of the request
   metadata (`*_request_metadata()`) minus `timeout_ms`. Different model,
   language, diarization, timestamps, pages, table format, … ⇒ not a duplicate.
4. **Recency** — `saved_at` within the look-back window
   (`--dedupe-window DAYS`, default 30, positive finite float).
5. **Artifact coverage** — the recorded files still exist on disk and cover
   what this run needs (`--format` and `--stdout` requirements). A md-only
   save is not a duplicate for a run that needs the JSON envelope.

Most recent qualifying entry wins.

## Persistence: `~/.mistral/index.ndjson`

Append-only NDJSON, one entry per saved result, written by the batch runner
after a successful `ResultStore.save()` (never with `--no-save`). File mode
0600; single atomic `O_APPEND` write + fsync. Absolute artifact paths, so
`--output-dir` saves dedupe globally. Entry shape (index schema_version 1):

    {"schema_version":1,"operation":"transcription","content_key":"sha256:…",
     "request_fingerprint":"…","source":{"kind":"file","value":"/abs/a.mp3",
     "filename":"a.mp3"},"model":"voxtral-mini-latest",
     "saved_at":"2026-07-04T10:00:00Z",
     "artifacts":{"markdown":"/abs/….md","json":"/abs/….json"}}

Robustness: corrupt/unknown lines are ignored on read; a missing index means
"no duplicates"; any index read/write `OSError` degrades to a stderr warning
(suppressed by `--quiet`) and normal processing — dedupe failures never fail
a run. Old results saved before this feature are simply never matched.

## CLI surface (both `ocr` and `transcribe`)

- `--force` — process even if an identical recent result exists (still
  records the new result in the index).
- `--dedupe-window DAYS` — look-back window, default `30.0`; must be a
  positive finite number (usage error otherwise). Large values ≈ forever.

## Skip behavior

- Happens *before* API-key resolution: a run where every source is a
  duplicate needs no key and exits 0.
- stderr (unless `--quiet`):
  `Skipping duplicate: SRC (existing result from TIMESTAMP; use --force to reprocess).`
  plus one `Existing: PATH` line per artifact.
- `--stdout`: the saved markdown file is re-emitted (sanitized/redacted, with
  the usual document separator). If it cannot be read, the source is
  processed normally instead of being skipped.
- `--json`: new NDJSON record (additive, record schema_version stays 1):

      {"schema_version":1,"status":"skipped","source":"a.mp3","reason":"duplicate",
       "existing":{"saved_at":"…Z","markdown":"/abs.md","json":"/abs.json",
                   "model":"voxtral-mini-latest"}}

- Summary: stderr becomes `Summary: X succeeded, Y failed, Z skipped.`; the
  summary record gains a required `skipped` integer.
- `--dry-run`: the (free, local) duplicate check runs too; stderr says
  `Would skip (duplicate): …` and the `dry_run` record gains an optional
  `duplicate` object (same shape as `existing`). Dry-run counts stay in
  `succeeded`.
- Skipped sources are neither successes nor failures; skips alone ⇒ exit 0.
- Hashing failure (unreadable file) is a normal per-source failure.

## Architecture

New module `src/mistral_cli/dedupe.py` (same layer as `storage.py`;
imports `models`, `errors`, `storage.utc_now` only):

- `content_key(source: InputSource) -> str` (raises `InputError` if unreadable)
- `request_fingerprint(metadata: JSONMapping) -> str`
- `DedupeMatch` frozen dataclass: `saved_at: datetime`,
  `markdown: Path | None`, `json: Path | None`, `model: str | None`
- `DedupeIndex(path, clock=utc_now)` with
  `lookup(*, operation, content_key, request_fingerprint, window_days,
  require_markdown, require_json) -> DedupeMatch | None` and
  `record(*, operation, content_key, request_fingerprint, source, model,
  saved: SavedResult) -> None`
- `DEDUPE_INDEX_FILENAME = "index.ndjson"`

Integration: `ResultStore` exposes `base_dir`; the batch runner builds
`DedupeIndex(store.base_dir / DEDUPE_INDEX_FILENAME)` lazily, consults it in
`_process_source`/`_dry_run_source`, and records after save. `run_batch()`
gains a `DedupeOptions(force, window_days)` parameter; `BatchPlan` requests
expose their `InputSource` via a `SourcedRequest` protocol bound on the
request TypeVar. `formatters.py` gains `build_existing_result()` /
`build_skipped_record()`; `build_summary_record()` and
`build_dry_run_record()` extend as above; `schema.py` follows.

## Non-goals

- No cross-process locking (two concurrent first runs may both transcribe).
- No index compaction (entries are ~300 B; stale entries are ignored).
- No fetching/hashing of remote URL bytes.
- No config-file setting for the window (CLI flag only).
