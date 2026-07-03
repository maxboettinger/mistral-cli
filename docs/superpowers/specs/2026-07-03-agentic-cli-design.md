# Agentic CLI usage — design

Date: 2026-07-03
Status: approved scope (user selected all four improvement areas); detailed
choices follow the recommended options presented during brainstorming.

## Goal

Make `mistral` a first-class tool for LLM agents: structured stdout an agent
can parse in one stream, machine-readable errors, stable exit codes,
self-describing documentation and schema, and noise/cost controls — without
changing default human-facing behavior and without weakening the two
cross-cutting invariants (security boundary, stdout/stderr discipline).

Research grounding (2025–2026 guidance from agentsurface.dev, InfoQ "Patterns
for AI Agent Driven CLIs", dev.to, Speakeasy, Firecrawl): agent-friendly CLIs
provide `--json` structured output treated as a versioned API contract,
semantic exit codes stable across versions, programmatic schema discovery,
shipped-with-the-tool agent guidance, non-interactive operation, dry runs, and
compact help.

## 1. `--json` NDJSON output on `ocr` and `transcribe`

New flag `--json` on both batch commands.

- Mutually exclusive with `--stdout` → usage error (exit 2).
- Writes one JSON record per line to stdout, streamed as each source
  completes. stderr behavior is unchanged (progress still goes there unless
  `--quiet`).
- Serialization: `json.dumps(..., ensure_ascii=True, allow_nan=False,
  separators=(",", ":"))` + `"\n"`. Pure-ASCII output means the
  `sanitize_terminal_text` boundary is a no-op on it (no mangling risk) and
  terminal-control injection through response content is impossible.
- All payloads pass the existing redaction path (`redact_result`, message
  redaction) *before* serialization, same as saved files.

### Record shapes (record `schema_version: 1`)

Success (per source):

```json
{"schema_version":1,"status":"ok","source":"<original CLI arg>",
 "envelope":{...the existing saved-JSON envelope, unchanged...},
 "saved":{"markdown":"/abs/path.md","json":"/abs/path.json"}}
```

`saved` values are `null` when that format was not written (`--format`,
`--no-save`).

Failure (per source, in-band — agents parse a single stream):

```json
{"schema_version":1,"status":"error","source":"<arg>",
 "error":{"code":"api_error","message":"<translated safe message>",
          "status_code":429}}
```

`source` is `null` for setup-level failures (e.g. missing API key), which are
also emitted as a record before the command exits. `status_code` is `null`
when unknown.

Dry run (per source, see §4):

```json
{"schema_version":1,"status":"dry_run","source":"<arg>",
 "request":{...request metadata, secrets/paths stripped...}}
```

Run summary (the final record of every completed run; a setup failure aborts
the run after emitting only its error record):

```json
{"schema_version":1,"status":"summary","succeeded":2,"failed":1}
```

### Error codes

New `error_code(error: MistralCliError) -> str` in `errors.py`, mapping the
existing taxonomy: `InputError → "input_error"`, `ConfigError →
"config_error"`, `ApiError → "api_error"`, `PersistenceError →
"persistence_error"`, base `MistralCliError → "unexpected_error"`. Codes are
part of the stable contract.

## 2. Exit codes (stable contract)

| Code | Meaning |
| --- | --- |
| 0 | every source succeeded (or every source validated, under `--dry-run`) |
| 1 | at least one source failed |
| 2 | usage / argument error (click default) |
| 3 | setup failure: API key missing/unresolvable, config unreadable |

Constants live in `errors.py` (`EXIT_FAILURE = 1`, `EXIT_USAGE = 2`,
`EXIT_SETUP = 3`). `resolve_api_key` and runtime creation switch from
`Exit(1)` to `Exit(3)`. Documented in `mistral agent` output and README;
changes only require a major version bump.

## 3. Discovery: `mistral agent` command

- `mistral agent` prints a compact agent-usage guide (Markdown) to stdout.
  The guide is a package data file (`src/mistral_cli/data/agent_guide.md`,
  read via `importlib.resources`) so it ships in the wheel and is retrievable
  on any install. Content: condensed, updated version of the
  `using-mistral-cli` skill — the two output facts, setup, command tables,
  NDJSON record shapes, error codes, exit codes, jq recipes.
- `mistral agent --schema` prints a JSON Schema (draft 2020-12) document
  describing the NDJSON records (including the nested envelope) instead of
  the guide. The schema is defined in code (`schema.py`) next to the envelope
  builder so it cannot silently drift from a data file; tests assert emitted
  records match the schema's required keys.
- The root group help gets an epilog: `Run 'mistral agent' for agent-oriented
  usage docs; 'mistral agent --schema' for the JSON output schema.` Both
  subcommands mention `--json` in their help. This makes the full surface
  discoverable from `mistral --help` alone.

## 4. Ergonomics flags on `ocr` and `transcribe`

- `--quiet`: suppresses `Processing:`/`Saved:`/`Summary:` stderr lines.
  Error reports are still written. Independent of `--json`.
- `--no-save`: skip writing result files entirely. Requires `--json` or
  `--stdout` (otherwise the result would be discarded → usage error, exit 2).
  Conflicts with `--output-dir` (usage error). `--format` is ignored.
- `--dry-run`: resolve each source and build/validate the request, then stop —
  no network call, no save, and **no API key required** (runtime creation is
  skipped). Human mode: writes `Would process: <source> (model …)` lines to
  stderr. JSON mode: emits `dry_run` records. Exit 0 if all sources validate,
  1 otherwise.

## 5. Refactor: shared batch runner

`cli/ocr.py` and `cli/transcribe.py` currently duplicate a ~100-line
per-source loop; the new flags would double the duplication. Extract a shared
runner (`cli/runner.py`):

- `run_batch(context, sources, *, plan, output_options)` where `plan`
  supplies: operation label strings, `build_request(source_value) ->
  request`, `create_service(api_key)`, and `format_markdown(result) -> str`.
- The runner owns: per-source try/except, progressive secret collection,
  lazy runtime creation, redaction, save, stdout emission (Markdown or
  NDJSON), stderr progress with `--quiet`, dry-run short-circuit, the summary
  (text or record), and the exit-code decision.
- `ocr.py`/`transcribe.py` keep their click declarations, option
  normalization, and the `create_gateway`/`create_result_store` test seams.

NDJSON record construction lives in `formatters.py`
(`build_ok_record`/`build_error_record`/`build_dry_run_record`/
`build_summary_record` + `serialize_ndjson`), keeping serialization concerns
in one module.

## Error handling

- Setup failure with `--json`: emit an `error` record (`source: null`), then
  exit 3. Without `--json`: current stderr message, exit 3.
- Per-source failures never stop the batch (unchanged), and with `--json`
  appear as in-band records; the stderr error report is also kept (agents
  that only read stdout are covered; humans piping still see errors).
- JSON serialization failure of a record is a `persistence_error`-class
  event: report to stderr, count the source as failed, continue.

## Testing

- Extend `test_ocr_cli.py`/`test_transcribe_cli.py` (fake gateways, CliRunner):
  `--json` happy path parses as NDJSON and matches record shapes; mixed
  batch produces ok+error+summary records and exit 1; `--json --stdout`
  → exit 2; `--quiet` silences progress but not errors; `--no-save` writes
  no files and requires an output flag; `--dry-run` makes no gateway calls,
  needs no API key, honors both output modes.
- New `tests/test_agent_cli.py`: guide prints and is non-empty/contains key
  sections; `--schema` parses as JSON Schema and its required record keys
  match records produced by the formatters.
- Exit-code tests for 0/1/2/3 paths.
- ASCII guarantee test: response containing control chars / ANSI sequences
  round-trips through `--json` into parseable JSON with content preserved
  (escaped), not stripped.

## Out of scope / follow-ups

- Updating `~/.claude/skills/using-mistral-cli` to teach the new flags
  (user-level file; do after implementation lands).
- MCP server exposure — the research mentions it, but the skill + NDJSON
  contract covers current agent harnesses; revisit if needed.
- Cost estimation in `--dry-run` (would require local page counting).
