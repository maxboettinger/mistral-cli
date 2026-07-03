# mistral CLI — agent usage guide

`mistral` wraps two Mistral capabilities: `ocr` (documents/images -> Markdown/JSON)
and `transcribe` (audio -> text). Both commands share one output and error model.

## Core facts

1. Results are saved to disk by default (`.md` and/or `.json` under
   `~/.mistral/ocr/` or `~/.mistral/transcriptions/`, or `--output-dir`).
   Use `--no-save` (with `--json` or `--stdout`) to skip files entirely.
2. stdout carries only results: rendered Markdown with `--stdout`, or NDJSON
   records with `--json` (mutually exclusive). Progress, saved paths, errors,
   and the summary go to stderr. `--quiet` silences progress; errors remain.

## Setup

API key resolution: `MISTRAL_API_KEY` env var, else `~/.mistral/config.toml`.

    printf '%s\n' "$KEY" | mistral config set api-key --stdin

Never pass the key as a CLI argument; the CLI refuses it. All output, including
`--debug` tracebacks, is redacted. `--dry-run` needs no API key.

## Commands

    mistral ocr [OPTIONS] SOURCE...
    mistral transcribe [OPTIONS] SOURCE...

`SOURCE...` mixes local paths and http(s) URLs. Failures never stop the batch.

Recommended agent invocation:

    mistral ocr --json --quiet doc.pdf
    mistral transcribe --json --quiet --diarize call.mp3

Key `ocr` options: `--pages 0,2-4` (0-indexed), `--table-format markdown|html`,
`--include-images`, `--include-blocks`, `--confidence page|word`,
`--extract-header`, `--extract-footer`, `--model` (default `mistral-ocr-latest`).

Key `transcribe` options: `--diarize`, `--timestamps segment|word` (repeatable),
`--language CODE` (cannot combine with `--timestamps`), `--context-bias TEXT`
(repeatable), `--model` (default `voxtral-mini-latest`).

Shared options: `--json`, `--stdout`, `--quiet`, `--no-save`, `--dry-run`,
`--output-dir DIR`, `--format md|json|both` (saved files), `--timeout SECONDS`.

## --json NDJSON contract (stable, schema_version 1)

One JSON object per line on stdout, streamed per source, ASCII-encoded.
Get the full JSON Schema with `mistral agent --schema`.

Success:

    {"schema_version":1,"status":"ok","source":"doc.pdf",
     "envelope":{"schema_version":1,"created_at":"...","source":{...},
                 "request":{...},"response":{...},"cli_version":"..."},
     "saved":{"markdown":"/path.md","json":"/path.json"}}

Failure (in-band; `source` is null for setup failures):

    {"schema_version":1,"status":"error","source":"bad.pdf",
     "error":{"code":"api_error","message":"...","status_code":429}}

Dry run:

    {"schema_version":1,"status":"dry_run","source":"doc.pdf","request":{...}}

Final record of every completed run:

    {"schema_version":1,"status":"summary","succeeded":2,"failed":1}

Error codes: `input_error`, `config_error`, `api_error` (with HTTP
`status_code` when known), `persistence_error`, `unexpected_error`.

## Exit codes (stable)

    0  every source succeeded (or validated, under --dry-run)
    1  at least one source failed
    2  usage error (bad flags or flag combination)
    3  setup failure (missing API key, unreadable config)

## jq recipes

    # Plain OCR/transcript text from the raw API response
    mistral transcribe --json --quiet a.mp3 | jq -r 'select(.status=="ok") | .envelope.response.text'
    mistral ocr --json --quiet doc.pdf | jq -r 'select(.status=="ok") | .envelope.response.pages[].markdown'

    # Collect failures with codes
    ... | jq -c 'select(.status=="error") | {source, code: .error.code}'

    # Batch outcome
    ... | jq 'select(.status=="summary")'

## Cost and scope control

OCR bills per page: restrict with `--pages`, and avoid `--include-images`,
`--include-blocks`, `--confidence` unless needed. Validate a batch first with
`--dry-run --json` (no network, no key).
