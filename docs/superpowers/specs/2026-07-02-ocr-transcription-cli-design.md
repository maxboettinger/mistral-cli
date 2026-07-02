# Mistral OCR and Transcription CLI Design

## Objective

Build a first-class Python command-line interface for Mistral's OCR and
offline transcription APIs. The initial release focuses on local files and
HTTP(S) URLs, durable Markdown and JSON results, secure local configuration,
and an architecture that can accommodate more Mistral APIs without coupling
the command layer to the generated SDK.

## Supported Environment

- Python 3.11 or newer
- The current `mistralai` v2 SDK
- Click for command parsing and help
- Rich for terminal presentation
- TOML configuration at `~/.mistral/config.toml`

The package uses a `src/` layout and exposes a `mistral` console script.

## Command Surface

### Configuration

```text
mistral config set api-key
mistral config set api-key --stdin
mistral config show
mistral config unset api-key
mistral config path
```

`config set api-key` reads the secret from a hidden prompt. `--stdin` supports
non-interactive configuration without placing a secret in process arguments or
shell history. `config show` always redacts the key. `config path` prints the
effective configuration path.

The API key resolution order is:

1. `MISTRAL_API_KEY`
2. `api_key` in the TOML configuration file

The root command accepts `--config PATH` for isolated environments and tests.
The default remains `~/.mistral/config.toml`.

### OCR

```text
mistral ocr [OPTIONS] SOURCE...
```

Each source is either a readable local regular file or an HTTP(S) URL. Relevant
options are:

- `--model` with default `mistral-ocr-latest`
- `--pages` using the API's zero-based range syntax, such as `0,2-4`
- `--table-format inline|markdown|html`
- `--extract-header`
- `--extract-footer`
- `--include-images`
- `--image-limit INTEGER`
- `--image-min-size INTEGER`
- `--include-blocks`
- `--confidence none|page|word`
- `--output-dir PATH`
- `--format md|json|both`, defaulting to `both`
- `--timeout SECONDS`
- `--stdout`

`inline` omits the API `table_format` value so tables remain inline in page
Markdown. Image limit and minimum size require `--include-images`.

### Transcription

```text
mistral transcribe [OPTIONS] SOURCE...
```

Each source is either a readable local regular file or an HTTP(S) URL. Relevant
options are:

- `--model` with default `voxtral-mini-latest`
- `--language CODE`
- `--temperature FLOAT`
- `--diarize`
- repeatable `--context-bias TEXT`, limited to 100 values
- repeatable `--timestamps segment|word`
- `--output-dir PATH`
- `--format md|json|both`, defaulting to `both`
- `--timeout SECONDS`
- `--stdout`

The CLI rejects `--language` together with `--timestamps`, matching the current
Mistral documentation. Repeated timestamp values are de-duplicated while
retaining their order. Temperature must be finite, but the CLI does not impose
an undocumented numeric range. Timeout values must be greater than zero.

### Root Behavior

The root command provides `--version`, `--debug`, `--config PATH`, and standard
help. Normal errors are concise and do not include tracebacks. `--debug`
preserves full exception context for diagnosis. Secrets are redacted in both
modes.

## Output Persistence

OCR results default to `~/.mistral/ocr`. Transcription results default to
`~/.mistral/transcriptions`. A custom `--output-dir` applies to the active
command.

For each successful source, the CLI creates:

```text
<UTC timestamp>-<original filename>.md
<UTC timestamp>-<original filename>.json
```

The timestamp is filesystem-safe, sortable, has microsecond precision, and
ends in `Z`. The original local filename is preserved after sanitizing path
separators and control characters. URL filenames are derived from the final
decoded path component; a deterministic `remote-document` or `remote-audio`
fallback is used when the URL has no filename.

The JSON document is a stable envelope:

```json
{
  "schema_version": 1,
  "created_at": "2026-07-02T12:34:56.123456Z",
  "source": {
    "kind": "file",
    "value": "/path/to/input.pdf",
    "filename": "input.pdf"
  },
  "request": {},
  "response": {},
  "cli_version": "0.1.0"
}
```

The request object contains effective non-secret API options. The response is
the complete JSON-serializable SDK response. API keys and internal
configuration paths are excluded.

OCR Markdown contains provenance followed by each page's Markdown in order,
with HTML comments marking page boundaries. Requested headers and footers are
preserved adjacent to their page. Transcription Markdown contains provenance
and either the complete transcript text or timestamped, speaker-labelled
segments when segment data is returned.

`--stdout` prints Markdown after it has been saved. It does not disable
persistence. With multiple sources, documents are separated by a visible
Markdown boundary.

Writes are atomic and collision-safe. Files are created in the destination
directory only after a complete API response and successful serialization.
Existing files are never overwritten.

## Architecture

```text
src/mistral_cli/
├── __init__.py
├── __main__.py
├── cli/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── ocr.py
│   └── transcribe.py
├── config.py
├── console.py
├── errors.py
├── formatters.py
├── models.py
├── mistral_client.py
├── services/
│   ├── __init__.py
│   ├── ocr.py
│   └── transcription.py
├── sources.py
└── storage.py
```

- `cli/` defines Click commands, validates command relationships, invokes use
  cases, and renders user-facing status.
- `config.py` owns typed configuration loading, key resolution, validation,
  redaction, atomic TOML writes, and permissions.
- `console.py` owns stdout and stderr Rich consoles so presentation remains
  consistent and testable.
- `models.py` contains immutable internal request, source, and persisted-result
  types that do not depend on Click or SDK-generated classes.
- `mistral_client.py` is the only module coupled to `mistralai`. It maps
  internal requests to the current SDK and converts responses to plain Python
  data.
- `services/` orchestrates OCR and transcription use cases through a gateway
  protocol.
- `sources.py` parses and validates local files and HTTP(S) URLs and builds the
  SDK input representation.
- `formatters.py` deterministically converts plain result data into Markdown.
- `storage.py` creates names and writes JSON and Markdown atomically.
- `errors.py` defines domain errors and maps expected SDK, network, input, and
  configuration failures to actionable CLI messages.

Dependencies point inward: command modules depend on internal services and
models; services depend on gateway protocols; only the adapter imports the
Mistral SDK.

For OCR, local files are represented as base64 data URLs. Their standard MIME
type is inferred without rejecting unknown extensions. Image MIME types map to
the SDK's `image_url` document variant; all other local files map to
`document_url`. URL sources with a recognized image extension use `image_url`;
other URLs use `document_url`. This default favors PDFs and office documents,
the primary CLI use case, while still supporting common image inputs.

For transcription, local sources are passed to the SDK as open binary streams
with their original filename, avoiding a whole-file read. URL sources use the
SDK's `file_url` parameter.

## Processing Flow

For each source:

1. Click parses basic types and flags.
2. The command validates cross-option constraints.
3. Configuration resolves the API key without displaying it.
4. Source parsing produces a validated local-file or URL value.
5. A service invokes the gateway with an internal request object.
6. The SDK adapter performs the API call and converts the response to plain
   data.
7. A formatter creates Markdown.
8. Storage constructs the JSON envelope and atomically writes the requested
   formats.
9. The CLI reports saved paths and optionally writes Markdown to stdout.

Multiple sources are processed sequentially. A failure for one source is
reported and processing continues. Successful outputs remain valid; the
command exits nonzero if any source failed and prints a compact final summary.

## Error Handling

- Missing configuration explains both supported API-key sources and the
  configuration command.
- Invalid paths, unsupported URL schemes, malformed page ranges, negative
  image values, excessive context-bias values, non-finite temperatures, and
  incompatible transcription options fail before API access.
- Authentication, rate-limit, validation, server, timeout, and connection
  failures are translated into concise domain errors when the SDK exposes
  enough information.
- Rich status output is written to stderr. Rich's terminal detection suppresses
  spinners and ANSI control sequences in redirected output.
- Keyboard interruption exits with the conventional interrupted status and
  does not leave partially written result files.
- Raw API response bodies are not shown unless debug mode is enabled, and
  known secrets are redacted before display.

## Configuration Safety

The configuration directory is created with mode `0700`. The configuration
file is written with mode `0600` through a same-directory temporary file and
atomic replacement. Existing configuration keys are preserved when one value
changes. TOML is parsed strictly; malformed configuration produces an
actionable error rather than silently discarding values.

The schema begins with:

```toml
config_version = 1
api_key = "..."
```

A key registry in the config command maps CLI names to typed configuration
fields. Adding future scalar settings does not require redesigning command
parsing or persistence.

## Testing and Quality Gates

Tests do not make live API requests. They inject fake gateways and temporary
configuration/output paths.

Coverage includes:

- configuration defaults, overrides, malformed TOML, redaction, atomic
  replacement, and POSIX permissions
- API-key environment precedence
- local and URL source parsing and filename sanitization
- UTC output naming and collision handling
- JSON envelopes and OCR/transcription Markdown
- exact OCR and transcription SDK request mapping
- all cross-option validation
- single and multi-source command behavior
- partial batch failure and exit status
- stdout/stderr separation
- Click help, version, configuration, and expected error output

Required local quality gates are:

```text
pytest
ruff check .
ruff format --check .
pyright
```

An optional manual smoke test may use a real API key and small fixture, but it
is not part of the automated suite because it would incur cost and depend on
network availability.

## Documentation

The README will contain installation with `uv tool install` and `pipx`,
configuration, OCR and transcription examples, option highlights, output
layout, security behavior, development commands, and links to the official
Mistral OCR and transcription documentation.

## Explicitly Deferred

- Mistral file IDs and file-management commands
- realtime or SSE transcription
- OCR annotations and custom JSON schemas
- extracted image/table asset materialization
- concurrent batch processing
- shell completion installation helpers
- live API integration tests

These can be added without changing the service, adapter, or persistence
boundaries.
