# Noridoc: cli

Path: @/src/moxtral/cli

### Overview

The Click command layer and the outermost entry point of the `mistral` console
script (`moxtral.cli.main:cli`). It defines the root group plus the `ocr`,
`transcribe`, and `config` commands, wires together sources, services, gateways,
and storage for each source, and owns all user-facing terminal output and its
secret-redaction boundary.

### How it fits into the larger codebase

This is the top of the dependency graph: command modules import
[`sources.py`](../sources.py), [`models.py`](../models.py),
[`services/`](../services/docs.md), [`mistral_client.py`](../mistral_client.py),
[`formatters.py`](../formatters.py), [`storage.py`](../storage.py),
[`dedupe.py`](../dedupe.py), and [`config.py`](../config.py). `ocr.py` and
`transcribe.py` build the request and hand everything else — including the
duplicate check — to the shared `runner.py` batch loop, which assembles the
request→duplicate-check→service→client→storage flow. Everything the user sees
on stdout/stderr passes through the [`ConsoleBundle`](../console.py) held on
the shared `AppContext`. The `create_gateway` / `create_result_store` factory
functions in `ocr.py` and `transcribe.py` are the seams tests patch to inject
fakes.

### Core Implementation

`main.py` defines the root `cli` group with `--version`, `--debug`, and
`--config PATH`. It stores a frozen `AppContext` (config path, debug flag, and a
`ConsoleBundle`) on `ctx.obj`, which every subcommand receives via
`@click.pass_obj`.

`ocr.py` and `transcribe.py` are near-mirrors. Each declares its Click
options, converts sentinel choices (e.g. OCR `--table-format inline` /
`--confidence none` become `None`), and hands off to `run_batch()` in
`runner.py` with a `BatchPlan` (operation, request builder, service/store
factories, markdown formatter), an `OutputOptions`
(`--format`/`--output-dir`/`--stdout`/`--json`/`--quiet`/`--no-save`/
`--dry-run`), and a `DedupeOptions` (`--force`, `--dedupe-window`). Both also
declare a `--retries N` option (default 3, validated nonnegative) that flows
into `build_ocr_request`/`build_transcription_request`; the actual retry
behavior against the API lives in
[`mistral_client.py`](../mistral_client.py), not here — this layer only
carries the count through.

`runner.py`'s `_BatchRun` then loops over `SOURCE...` values sequentially:

```
resolve_source ─▶ build_*_request ─▶ [unless --force] index.lookup() — a
   match skips the source ─▶ else lazily build _Runtime: resolve api key,
   create service ─▶ service.run ─▶ redact_result ─▶ format_*_markdown ─▶
   store.save ─▶ index.record() ─▶ print "Saved:" paths (stderr)
```

The result store and the duplicate index (`dedupe.DedupeIndex`, rooted at
`store.base_dir / index.ndjson`) are created lazily but independently of the
`_Runtime`, so the duplicate check runs — and can skip a source — before any
API key is resolved. Unless `--force`, a match on content hash, request
fingerprint, recency (`--dedupe-window`), and artifact coverage skips the
source: a `Skipping duplicate: …` stderr line, the cached Markdown re-emitted
under `--stdout`, and a `status: "skipped"` record under `--json` — the
source is never sent to the API. The `_Runtime` (secrets, service) is still
created lazily, on the first source that is neither invalid nor a duplicate,
so a batch that is entirely duplicates or invalid never resolves a key. A
failure on one source is reported and counted; the loop continues. A final
`Summary: N succeeded, M failed, K skipped.` prints to stderr (with a
matching `skipped` count in the `status: "summary"` record), and any failure
raises `click.exceptions.Exit(1)` — skips count toward neither successes nor
failures. `--dry-run` runs the same free duplicate check and reports
`Would skip (duplicate): …` instead of processing.

`config.py` defines the `config` group (`set`, `show`, `unset`, `path`).
`config set api-key` reads the secret only from a hidden confirmed prompt or
`--stdin` — never from an argument; `allow_extra_args`/`ignore_unknown_options`
plus an explicit `context.args` check reject any positional value so a key can
never land in process arguments or shell history.

`common.py` holds the shared helpers all commands funnel through:
`candidate_secrets` (gathers env + configured keys to redact), `resolve_api_key`,
`redact_result` (recursively scrubs an `ApiResult`), `report_error` (translates
via [`errors.py`](../errors.py) and writes safe output), and `safe_terminal_text`
(sanitize control sequences + redact). It is also the shared home for small
Click option validators reused across command modules — `positive_days` and
`nonnegative_integer` (used by `ocr.py`'s `--image-limit`/`--image-min-size`
and both commands' `--retries`) — so validation logic for a shared option shape
lives in one place instead of being duplicated per command.

### Things to Know

Secret redaction is a strict invariant enforced here, not in the services. The
`ApiResult` is passed through `redact_result` *before* it is formatted, saved, or
echoed, so the persisted Markdown/JSON and the terminal both receive
already-scrubbed content; `safe_terminal_text` is applied again on every stderr/
stdout write as a second layer that also strips terminal control sequences.
`candidate_secrets` deliberately loads the configured key with `environ={}` and
swallows `ConfigError` so that a malformed config file cannot mask the real input
error the user is trying to diagnose. Progress, saved paths, summaries, and all
errors go to stderr; only `--stdout` Markdown goes to stdout, keeping piped
Markdown clean, with multiple documents separated by a `\n\n---\n\n` rule. The
root `--debug` flag causes `report_error` to additionally emit a redacted
traceback via [`errors.format_debug_exception`](../errors.py).

Duplicate skips are a third outcome, not a success or a failure: they need no
API key, never contribute to `Exit(1)`, and are tallied separately in both the
stderr summary and the `status: "summary"` record's `skipped` field.
`--no-save` runs never write to the duplicate index, and `--force` always
reprocesses (still recording the refreshed result) regardless of what the
index shows.

Created and maintained by Nori.
