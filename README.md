# mistral-cli

A modern command-line interface for extracting structured content with
[Mistral OCR](https://docs.mistral.ai/studio-api/document-processing/basic_ocr)
and transcribing audio with
[Mistral Speech-to-Text](https://docs.mistral.ai/studio-api/audio/speech_to_text/offline_transcription).

It accepts local files and public HTTP(S) URLs, saves durable Markdown and JSON
results, supports batch processing, and keeps progress output separate from
pipe-friendly Markdown output.

## Requirements

- Python 3.11 or newer
- A Mistral API key

## Installation

Install as an isolated command with [uv](https://docs.astral.sh/uv/):

```console
uv tool install .
```

Alternatively, use [pipx](https://pipx.pypa.io/):

```console
pipx install .
```

For development from a source checkout:

```console
uv sync
uv run mistral --help
```

## Configuration

Store an API key using a hidden, confirmed prompt:

```console
mistral config set api-key
```

For automation, pass a single line over standard input. For example, this
reads the key without echoing it or putting it in the command's argument list:

```console
read -r -s MISTRAL_KEY
printf '%s\n' "$MISTRAL_KEY" | mistral config set api-key --stdin
unset MISTRAL_KEY
```

An API key provided through `MISTRAL_API_KEY` takes precedence over the
configured value:

```console
MISTRAL_API_KEY="..." mistral ocr report.pdf
```

Inspect or change the stored configuration without exposing the key:

```console
mistral config show
mistral config path
mistral config unset api-key
```

The default configuration path is `~/.mistral/config.toml`. The CLI creates
its configuration directories with mode `0700` and the file with mode `0600`
on POSIX systems, and writes updates atomically. The key is still stored as
plaintext, so protect the account and backups that contain this file. Use the
root `--config FILE` option to select another configuration file:

```console
mistral --config ./private-config.toml config set api-key
```

## OCR

Process a local PDF:

```console
mistral ocr report.pdf
```

Process a public document or image URL:

```console
mistral ocr https://example.com/report.pdf
```

Public URLs must be directly accessible to Mistral. Local files are read and
sent as base64 data URLs.

Useful OCR options include:

```console
mistral ocr report.pdf \
  --model mistral-ocr-latest \
  --pages 0,2-4 \
  --table-format markdown \
  --extract-header \
  --extract-footer \
  --include-images \
  --image-limit 20 \
  --image-min-size 100 \
  --include-blocks \
  --confidence word \
  --timeout 300
```

- `--pages TEXT` accepts zero-based page numbers and ascending ranges such as
  `0,2-4`.
- `--table-format inline|markdown|html` controls table representation;
  `inline` is the default.
- `--extract-header` and `--extract-footer` return those regions separately.
- `--include-images` requests base64 image data. `--image-limit` and
  `--image-min-size` require it and accept nonnegative integers.
- `--include-blocks` requests structured blocks and bounding boxes.
- `--confidence none|page|word` controls confidence detail.
- `--model` selects the OCR model; the default is `mistral-ocr-latest`.
- `--timeout` is a positive request timeout in seconds and defaults to `300`.

Some options depend on model capabilities. See the official OCR documentation
for current model requirements.

## Transcription

Transcribe a local audio file:

```console
mistral transcribe interview.mp3
```

Transcribe a public audio URL:

```console
mistral transcribe https://example.com/interview.mp3
```

Request speaker labels, contextual hints, and timestamps:

```console
mistral transcribe interview.mp3 \
  --model voxtral-mini-latest \
  --temperature 0.2 \
  --diarize \
  --context-bias Mistral \
  --context-bias Voxtral \
  --timestamps segment \
  --timestamps word \
  --timeout 300
```

- `--language CODE` supplies the source language.
- `--temperature FLOAT` controls sampling and must be finite.
- `--diarize` requests speaker identification.
- `--context-bias TEXT` is repeatable, with at most 100 values.
- `--timestamps segment|word` is repeatable; duplicate values are ignored.
- `--model` selects the transcription model; the default is
  `voxtral-mini-latest`.
- `--timeout` is a positive request timeout in seconds and defaults to `300`.

`--language` and `--timestamps` cannot be combined because the Mistral API
does not support that combination.

## Results and batch processing

Both processing commands share these output options:

- `--format md|json|both` chooses saved formats and defaults to `both`.
- `--output-dir DIRECTORY` overrides the operation's default result directory.
- `--stdout` also writes the rendered Markdown to standard output. Progress,
  saved paths, summaries, and errors remain on standard error.

By default, results are written to:

```text
~/.mistral/ocr/
~/.mistral/transcriptions/
```

Names use a UTC timestamp followed by the original source filename:

```text
20260703T121314.123456Z-report.pdf.md
20260703T121314.123456Z-report.pdf.json
```

Markdown contains provenance and readable page or transcript content. JSON is
a stable envelope with `schema_version`, `created_at`, source metadata, request
options, the complete API response, and `cli_version`.

Pass multiple local files and URLs to process them sequentially:

```console
mistral ocr chapter-1.pdf chapter-2.pdf https://example.com/appendix.pdf
mistral transcribe part-1.mp3 part-2.mp3
```

A failure for one source does not discard successful results or stop later
sources. The final summary reports successes and failures, and any failure
causes a nonzero exit status. When `--stdout` is used with multiple successful
sources, Markdown documents are separated by a horizontal rule.

## Errors, debugging, and security

Expected input, configuration, network, API, and persistence failures are
reported concisely without a traceback. Put the root `--debug` flag before the
command to include diagnostic tracebacks:

```console
mistral --debug transcribe interview.mp3
```

The CLI redacts known API keys from terminal output and persisted results,
including debug output. It never accepts an API key as a command-line value:
use the hidden prompt, `--stdin`, or `MISTRAL_API_KEY`. Avoid embedding other
credentials in source URLs because URLs are recorded as result provenance.

## Development

Install locked runtime and development dependencies:

```console
uv sync
```

Run the full quality gates:

```console
uv run pytest --cov=mistral_cli --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
uv build
```

The package uses a `src/` layout with clear boundaries:

- `cli/` defines Click commands, validation flow, and Rich console reporting.
- `models.py` contains SDK-independent request and result types.
- `services/` implements OCR and transcription use cases behind gateway
  protocols.
- `mistral_client.py` is the adapter for the official Mistral Python SDK.
- `config.py` handles typed TOML configuration and secure atomic writes.
- `sources.py`, `formatters.py`, and `storage.py` resolve inputs, render
  results, and persist collision-safe output atomically.
- `errors.py` and `console.py` translate, redact, and safely present failures.

Tests isolate the API behind fakes and cover command behavior, mapping,
formatting, configuration, security boundaries, and persistence.

## References

- [Mistral OCR](https://docs.mistral.ai/studio-api/document-processing/basic_ocr)
- [Mistral offline transcription](https://docs.mistral.ai/studio-api/audio/speech_to_text/offline_transcription)
- [Mistral audio transcription API endpoint](https://docs.mistral.ai/api/endpoint/audio/transcriptions)
- [Click documentation](https://click.palletsprojects.com/)
- [Rich documentation](https://rich.readthedocs.io/)
