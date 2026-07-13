# moxtral

[![CI](https://github.com/maxboettinger/moxtral/actions/workflows/ci.yml/badge.svg)](https://github.com/maxboettinger/moxtral/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A modern command-line interface for [Mistral OCR](https://docs.mistral.ai/studio-api/document-processing/basic_ocr) and [Voxtral audio transcription](https://docs.mistral.ai/studio-api/audio/speech_to_text/offline_transcription).**

> **Powered by [Mistral AI](https://mistral.ai)** — moxtral is an independent,
> open-source CLI built on the official Mistral AI platform APIs (Mistral OCR
> for documents, Voxtral for audio). It is **not affiliated with, endorsed by,
> or sponsored by Mistral AI.** See
> [About the name & trademarks](#about-the-name--trademarks).

Turn PDFs and images into structured Markdown, and turn audio into text — from
your terminal, in one command. Point it at a local file or a public URL; it
saves durable Markdown and JSON, processes batches, and keeps progress output
cleanly separated from pipe-friendly results.

```console
moxtral ocr report.pdf
moxtral transcribe interview.mp3
```

---

## Highlights

- **Two capabilities, one tool** — document OCR and speech-to-text behind a
  single `moxtral` command.
- **Local files or public URLs** — local files are read and sent as base64 data
  URLs; public HTTP(S) URLs are passed through.
- **Durable results** — every run saves timestamped Markdown and a stable JSON
  envelope you can diff, re-render, or feed to other tools.
- **Batch-friendly** — pass many sources at once; one failure never discards the
  successful results.
- **Skips duplicate work** — rerunning the same source with the same options
  within a recency window costs nothing: the CLI detects it and skips the API
  call; `--force` overrides.
- **Pipe-clean output** — Markdown goes to stdout on request; progress, paths,
  and errors stay on stderr.
- **Agent-ready** — `--json` streams stable NDJSON records for machine
  consumption, exit codes are documented and stable, and `moxtral agent`
  prints a compact usage guide and output schema for LLM agents.
- **Secure by default** — API keys are never passed as CLI arguments, are
  redacted from all output (including tracebacks), and are stored with
  restrictive file permissions.

## Requirements

- Python 3.11 or newer
- A [Mistral API key](https://console.mistral.ai/)

---

## Quickstart

**1. Install** as an isolated command with [uv](https://docs.astral.sh/uv/)
(or [pipx](https://pipx.pypa.io/)), always from a checkout of this
repository — this tool is not published on PyPI:

```console
git clone https://github.com/maxboettinger/moxtral.git
uv tool install ./moxtral
```

**Updating** after pulling or editing code: force a rebuild. A plain
`uv tool install .` may silently reuse a previously built wheel when the
version number hasn't changed, leaving the installed command stale:

```console
uv tool install --reinstall --force .
```

Then verify you are running this tool and that it picked up the new code:

```console
uv tool list          # the `moxtral` executable must be listed under `moxtral`
moxtral --help        # must show: agent, config, ocr, transcribe
```

> **Note:** this tool is not published on PyPI — the only install source is
> this repository. Don't confuse it with the PyPI package named `mistral`
> (OpenStack Mistral, a workflow service unrelated to Mistral AI) or with
> Mistral AI's own coding-agent CLI (`vibe`).

**2. Store your API key** (hidden, confirmed prompt):

```console
moxtral config set api-key
```

**3. Run it:**

```console
# Extract text from a PDF or image
moxtral ocr report.pdf

# Transcribe audio
moxtral transcribe interview.mp3
```

Results are saved to `~/.moxtral/ocr/` and `~/.moxtral/transcriptions/` as both
Markdown and JSON. Add `--stdout` to also print the Markdown for piping:

```console
moxtral ocr report.pdf --stdout | glow -
```

That's the whole loop. The sections below add detail as you need it.

---

## Usage

### OCR

Process a local PDF or image, or a publicly accessible URL:

```console
moxtral ocr report.pdf
moxtral ocr https://example.com/report.pdf
```

> Public URLs must be directly reachable by Mistral. Local files are read and
> sent as base64 data URLs. Local files are limited to 50 MB (they are sent
> inline as base64); larger documents should be hosted at a URL.

Common options:

```console
moxtral ocr report.pdf \
  --pages 0,2-4 \
  --table-format markdown \
  --extract-header \
  --extract-footer \
  --include-images --image-limit 20 --image-min-size 100 \
  --include-blocks \
  --confidence word
```

| Option | Description |
| --- | --- |
| `--pages TEXT` | Zero-based page numbers and ascending ranges, e.g. `0,2-4`. |
| `--table-format inline\|markdown\|html` | Table representation. Default: `inline`. |
| `--extract-header` / `--extract-footer` | Return those regions separately. |
| `--include-images` | Request base64 image data. |
| `--image-limit N` / `--image-min-size N` | Bound returned images (require `--include-images`; nonnegative). |
| `--include-blocks` | Request structured blocks and bounding boxes. |
| `--confidence none\|page\|word` | Confidence detail level. |
| `--model TEXT` | OCR model. Default: `mistral-ocr-latest`. |
| `--timeout SECONDS` | Positive request timeout. Default: `300`. |
| `--retries N` | Retry attempts for rate-limited, server-error, and connection failures, with exponential backoff. Default: `3`; `0` disables. |

Some options depend on model capabilities — see the
[official OCR documentation](https://docs.mistral.ai/studio-api/document-processing/basic_ocr).

### Transcription

Transcribe a local audio file or a public audio URL:

```console
moxtral transcribe interview.mp3
moxtral transcribe https://example.com/interview.mp3
```

Request speaker labels, contextual hints, and timestamps:

```console
moxtral transcribe interview.mp3 \
  --temperature 0.2 \
  --diarize \
  --context-bias Mistral --context-bias Voxtral \
  --timestamps segment
```

| Option | Description |
| --- | --- |
| `--language CODE` | Source language code. |
| `--temperature FLOAT` | Sampling temperature; must be finite. |
| `--diarize` | Request speaker identification. |
| `--context-bias TEXT` | Contextual hint; repeatable, up to 100 values. |
| `--timestamps segment\|word` | Repeatable; duplicates ignored. |
| `--model TEXT` | Transcription model. Default: `voxtral-mini-latest`. |
| `--timeout SECONDS` | Positive request timeout. Default: `300`. |
| `--retries N` | Retry attempts for rate-limited, server-error, and connection failures, with exponential backoff. Default: `3`; `0` disables. |

> `--language` and `--timestamps` cannot be combined — the Mistral API does not
> support that pairing.

---

## Results & batch processing

Both commands share these output options:

| Option | Description |
| --- | --- |
| `--format md\|json\|both` | Which formats to save. Default: `both`. |
| `--output-dir DIRECTORY` | Override the default result directory. |
| `--stdout` | Also write rendered Markdown to standard output. |
| `--json` | Write NDJSON result records to standard output (one per source; mutually exclusive with `--stdout`). |
| `--quiet` | Suppress progress and summary lines on stderr (errors still shown). |
| `--no-save` | Skip result files entirely (requires `--json` or `--stdout`). |
| `--dry-run` | Validate sources and options without calling the API — no key needed. |
| `--force` | Process a source even if an identical recent result already exists. |
| `--dedupe-window DAYS` | Look-back window for duplicate detection. Default: `30`. |

**Where results go.** By default, into per-command directories, named with a UTC
timestamp followed by the original source filename:

```text
~/.moxtral/ocr/20260703T121314.123456Z-report.pdf.md
~/.moxtral/ocr/20260703T121314.123456Z-report.pdf.json
~/.moxtral/transcriptions/...
```

- **Markdown** contains provenance and readable page or transcript content.
- **JSON** is a stable envelope: `schema_version`, `created_at`, source metadata,
  request options, the complete API response, and `cli_version`.

**Batch processing.** Pass multiple local files and URLs to process them
sequentially:

```console
moxtral ocr chapter-1.pdf chapter-2.pdf https://example.com/appendix.pdf
moxtral transcribe part-1.mp3 part-2.mp3
```

A failure for one source does not discard successful results or stop later
sources. The final summary reports successes, failures, and skips, and any
failure produces a nonzero exit status. With `--stdout` and multiple
successes, Markdown documents are separated by a horizontal rule.

**Duplicate skipping.** Before calling the API, each source is checked against
a local index of prior results and skipped if an identical, recent one already
exists — no charge, no network call:

- **Content** matches by SHA-256 of a local file's bytes (renames don't
  matter; different bytes never match) or by the literal value of a URL
  source.
- **Options** must match too — the same model, language, diarization,
  timestamps, pages, table format, etc. (`--timeout` is ignored).
- **Recency**: the existing result must be within `--dedupe-window DAYS`
  (default `30`).
- **Coverage**: the existing files must still be on disk and satisfy this
  run's `--format`/`--stdout` needs — a Markdown-only save doesn't dedupe a
  run that also needs the JSON envelope.

A skip prints `Skipping duplicate: SOURCE (existing result from TIMESTAMP;
use --force to reprocess).` plus one `Existing: PATH` line per artifact on
stderr, re-emits the saved Markdown under `--stdout`, and writes a
`status: "skipped"` record under `--json`. Pass `--force` to reprocess
regardless (this still records the new result). `--dry-run` runs the same
check for free and reports what it *would* skip. Duplicate checks need no API
key, so a run where every source is a duplicate exits `0` without ever
resolving a key.

The index lives at `~/.moxtral/index.ndjson` (created automatically, `0600`,
best-effort — a read/write problem falls back to processing normally with a
warning) and is never written to with `--no-save`.

---

## Machine-readable output & agents

With `--json`, each command streams one NDJSON record per source to stdout —
`status: "ok"` records carry the full result envelope and saved paths,
`status: "skipped"` records report a duplicate that was skipped instead of
billed (with the existing saved paths and timestamp), failures appear in-band
as `status: "error"` records with stable error codes (`input_error`,
`config_error`, `api_error`, `persistence_error`, `unexpected_error`), and
every completed run ends with a `status: "summary"` record. Output is
ASCII-encoded, so it is immune to terminal-control injection and always
parses:

```console
moxtral ocr --json --quiet report.pdf \
  | jq -r 'select(.status=="ok") | .envelope.response.pages[].markdown'
```

Exit codes are part of the stable contract:

| Code | Meaning |
| --- | --- |
| `0` | Every source succeeded or was skipped as a duplicate (or validated, under `--dry-run`). |
| `1` | At least one source failed. |
| `2` | Usage error (bad flags or flag combination). |
| `3` | Setup failure (missing API key, unreadable config). |

For LLM agents and other tooling, `moxtral agent` prints a compact usage guide
and `moxtral agent --schema` prints the JSON Schema for the NDJSON records.
Use `--dry-run --json` to validate a batch without an API key or network
calls — it also reports sources that would be skipped as duplicates.

---

## Configuration

### API key

The key is resolved from `MISTRAL_API_KEY` first, then the stored config.

Store it via a hidden, confirmed prompt:

```console
moxtral config set api-key
```

For automation, pass a single line over stdin (no echo, not in the arg list):

```console
read -r -s MISTRAL_KEY
printf '%s\n' "$MISTRAL_KEY" | moxtral config set api-key --stdin
unset MISTRAL_KEY
```

Or override per-invocation with the environment variable:

```console
MISTRAL_API_KEY="..." moxtral ocr report.pdf
```

Inspect or change the stored configuration without exposing the key:

```console
moxtral config show
moxtral config path
moxtral config unset api-key
```

### Config file

The default path is `~/.moxtral/config.toml`. On POSIX systems the CLI creates
its config directory with mode `0700` and the file with mode `0600`, and writes
updates atomically. The key is stored as **plaintext**, so protect the account
and any backups that contain this file.

Select a different file with the root `--config` option:

```console
moxtral --config ./private-config.toml config set api-key
```

### Shell completion

Click provides tab completion for commands and options. Add the line for
your shell to its startup file:

```console
eval "$(_MOXTRAL_COMPLETE=zsh_source moxtral)"     # ~/.zshrc
eval "$(_MOXTRAL_COMPLETE=bash_source moxtral)"    # ~/.bashrc
_MOXTRAL_COMPLETE=fish_source moxtral | source     # ~/.config/fish/config.fish
```

---

## Errors, debugging & security

Expected input, configuration, network, API, and persistence failures are
reported concisely, without a traceback. Put the root `--debug` flag **before**
the command to include diagnostic tracebacks:

```console
moxtral --debug transcribe interview.mp3
```

Transient failures that fail fast — HTTP 429 rate limits, 5xx server errors,
refused connections — are retried automatically with exponential backoff
(`--retries`, default 3). Requests that fail slowly (e.g. timeouts) exhaust
the retry budget and are not retried. A request that never succeeded is
never billed.

Security properties:

- The CLI **never** accepts an API key as a command-line value. Use the hidden
  prompt, `--stdin`, or `MISTRAL_API_KEY`.
- Known API keys are **redacted** from terminal output and persisted results,
  including debug output.
- Avoid embedding credentials in source URLs — URLs are recorded as result
  provenance.

---

## About the name & trademarks

moxtral (**M**istral **O**CR + Vo**xtral**) is an independent, open-source
project. It is not affiliated with, endorsed by, or sponsored by Mistral AI.
"Mistral", "Mistral OCR", and "Voxtral" are trademarks of their respective
owner and are used here only to identify the APIs this tool talks to.

The CLI deliberately does not claim the bare `mistral` command: that name
belongs to other projects (the OpenStack Mistral client on PyPI installs a
`mistral` executable) and plausibly to Mistral AI's own tooling in the future
(their coding agent currently installs `vibe`). `moxtral` installs alongside
all of them without conflict.

> Historical note: this project was named `mistral-cli` (command `mistral`)
> up to version 0.3.0.

---

## Development

Install locked runtime and development dependencies:

```console
uv sync
uv run moxtral --help
```

Run the full quality gates:

```console
uv run pytest --cov=moxtral --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv build
```

### Architecture

The package uses a `src/` layout with clear boundaries between the CLI surface,
domain models, use cases, and the SDK adapter:

| Module | Responsibility |
| --- | --- |
| `cli/` | Click commands, validation flow, and sanitized console reporting. |
| `models.py` | SDK-independent request and result types. |
| `services/` | OCR and transcription use cases behind gateway protocols. |
| `mistral_client.py` | Adapter for the official Mistral Python SDK. |
| `config.py` | Typed TOML configuration and secure atomic writes. |
| `sources.py` | Resolves local files and URLs into request inputs. |
| `formatters.py` | Renders results to Markdown. |
| `storage.py` | Persists collision-safe output atomically. |
| `errors.py`, `console.py` | Translate, redact, and safely present failures. |

The request flow is: **CLI** parses and validates options → **services** invoke
the use case through a gateway protocol → **mistral_client** calls the SDK →
**formatters** and **storage** render and persist results. Tests isolate the API
behind fakes and cover command behavior, mapping, formatting, configuration,
security boundaries, and persistence.

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## References

- [Mistral OCR](https://docs.mistral.ai/studio-api/document-processing/basic_ocr)
- [Mistral offline transcription](https://docs.mistral.ai/studio-api/audio/speech_to_text/offline_transcription)
- [Mistral audio transcription endpoint](https://docs.mistral.ai/api/endpoint/audio/transcriptions)
- [Click documentation](https://click.palletsprojects.com/)
