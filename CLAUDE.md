# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A `mistral` CLI wrapping Mistral OCR (documents/images → Markdown) and audio
transcription (audio → text). `src/` layout, packaged with hatchling, managed
with `uv`. Entry point: `mistral_cli.cli.main:cli`.

## Commands

```console
uv sync                              # install locked runtime + dev deps
uv run mistral --help                # run the CLI in-tree

uv run pytest                        # full test suite
uv run pytest tests/test_ocr_cli.py::test_name   # a single test
uv run pytest --cov=mistral_cli --cov-report=term-missing

uv run ruff check .                  # lint
uv run ruff format --check .         # format check (drop --check to apply)
uv run pyright                       # type check (strict mode)
uv lock --check && uv build          # verify lockfile + build artifacts
```

Type checking is **strict** (`typeCheckingMode = "strict"`, includes `tests`).
Every module uses `from __future__ import annotations`; domain types are frozen,
slotted dataclasses. Ruff selects `E,F,I,UP,B,SIM,RUF`.

## Architecture

Strict layering, dependencies point inward. The request flow:

**CLI** (`cli/`) parses & validates → **services/** run the use case via a
`Protocol` gateway → **mistral_client.py** adapts the official `mistralai` SDK →
**formatters.py** renders Markdown, **storage.py** persists.

| Module | Role |
| --- | --- |
| `cli/main.py` | Root `click.group`; builds `AppContext` (config path, debug, consoles). |
| `cli/ocr.py`, `cli/transcribe.py`, `cli/config.py`, `cli/agent.py` | Click commands (`agent` prints the packaged guide/schema). |
| `cli/runner.py` | Shared batch loop: per-source errors, lazy runtime, save, Markdown/NDJSON stdout, `--quiet`/`--no-save`/`--dry-run`, exit codes. |
| `cli/common.py` | Shared secret handling, error reporting, redaction. |
| `schema.py` | JSON Schema for the `--json` NDJSON records (`mistral agent --schema`). |
| `models.py` | SDK-independent request/result types + `build_*_request` validation. |
| `services/` | `OcrService` / `TranscriptionService` behind `*Gateway` protocols. |
| `mistral_client.py` | `MistralGateway`: the only place that touches `mistralai`. |
| `config.py` | `ConfigStore`: typed TOML, atomic writes, POSIX 0700/0600 perms. |
| `sources.py` | Resolves a source string to a local-file or URL `InputSource`. |
| `formatters.py` | Result → Markdown. |
| `storage.py` | `ResultStore`: collision-safe atomic saves to `~/.mistral/{ocr,transcriptions}/`. |
| `errors.py`, `console.py` | Translate, redact, and safely present failures. |

Services take a gateway `Protocol`, not the concrete SDK client — tests inject
fakes rather than mocking the network. The command modules expose
`create_gateway`/`create_result_store` seams for the same reason. Keep SDK
imports confined to `mistral_client.py`.

## Two cross-cutting invariants

**1. Security boundary — everything user-facing is sanitized and redacted.**
Any string reaching the terminal or disk must pass through
`safe_terminal_text()` (cli/common.py): it strips terminal control sequences
(`sanitize_terminal_text`) *and* redacts every known secret variant. Results are
run through `redact_result()` before formatting/saving. Secrets are collected
progressively — `candidate_secrets()` (env + stored key) before setup,
then `extend_secrets()` once the real key is resolved in the runner — so
error output is always redacted even on early failures. The CLI **never** accepts
an API key as an argument (prompt, `--stdin`, or `MISTRAL_API_KEY` only).
`translate_exception()` maps external/SDK errors to safe `MistralCliError`
messages so untrusted exception text never leaks; raw detail appears only under
`--debug`, still redacted.

**2. stdout vs stderr discipline.** stdout carries *only* pipe-friendly results
(rendered Markdown with `--stdout`, or NDJSON records with `--json` — mutually
exclusive). All progress, saved-path notices, errors, and the run summary go to
stderr (`--quiet` suppresses the non-error lines). Both go through
`ConsoleBundle` (`write_stdout`/`write_stderr`), which sanitizes on the way out.
NDJSON is serialized with `ensure_ascii=True` (`serialize_ndjson`), so
sanitization is a no-op on it and control-character injection is impossible.

**Stable machine contract.** The NDJSON record shapes (record builders in
`formatters.py` + `schema.py`), the error codes (`errors.error_code`), and the
exit codes (`errors.EXIT_FAILURE`/`EXIT_USAGE`/`EXIT_SETUP` = 1/2/3, 0 =
success) are a public contract documented by `mistral agent`; breaking changes
require bumping the record `schema_version`. Keep `src/mistral_cli/data/agent_guide.md`
in sync when the CLI surface changes.

## Command conventions

The command modules normalize options (`--table-format inline` /
`--confidence none` → `None`) and delegate the loop to `run_batch()`
(`cli/runner.py`), which owns per-source `try/except` (one failure never stops
later sources), lazy API-key/runtime setup, dry-run short-circuiting, stdout
emission, and the exit-code decision (`Exit(1)` on any source failure,
`Exit(3)` on setup failure). New request options are added to the
`build_*_request` validators in `models.py` and echoed into
`*_request_metadata()` so they land in the saved JSON envelope, the NDJSON
records, and `--dry-run` output.

## Notes

- `.recall/` holds a local session log (Recall); it's gitignored, not project code.
- Design/plan docs live in `docs/superpowers/`.
