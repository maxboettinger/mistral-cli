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
| `cli/ocr.py`, `cli/transcribe.py`, `cli/config.py` | Click commands. |
| `cli/common.py` | Shared secret handling, error reporting, redaction, API-key resolution. |
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
`resolve_api_key()` then `extend_secrets()` once the real key is known — so
error output is always redacted even on early failures. The CLI **never** accepts
an API key as an argument (prompt, `--stdin`, or `MISTRAL_API_KEY` only).
`translate_exception()` maps external/SDK errors to safe `MistralCliError`
messages so untrusted exception text never leaks; raw detail appears only under
`--debug`, still redacted.

**2. stdout vs stderr discipline.** stdout carries *only* pipe-friendly results
(rendered Markdown, and only with `--stdout`). All progress, saved-path notices,
errors, and the run summary go to stderr. Both go through `ConsoleBundle`
(`write_stdout`/`write_stderr`), which sanitizes on the way out.

## Command conventions

Batch commands loop over sources with a per-source `try/except`: a failure
increments `failures`, is reported, and does **not** stop later sources. Any
failure at the end raises `click.exceptions.Exit(1)`. Options that map to `None`
when "off" (e.g. `--table-format inline`, `--confidence none`) are normalized in
the command before building the request. New request options are added to the
`build_*_request` validators in `models.py` and echoed into the service's
`request_metadata` so they land in the saved JSON envelope.

## Notes

- `.recall/` holds a local session log (Recall); it's gitignored, not project code.
- Design/plan docs live in `docs/superpowers/`.
