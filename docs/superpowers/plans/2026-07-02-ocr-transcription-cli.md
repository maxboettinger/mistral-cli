# Mistral OCR and Transcription CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an installable `mistral` Python CLI that securely configures an API key, processes local files and URLs through Mistral OCR or transcription, and atomically archives Markdown and JSON results.

**Architecture:** A `src/` package keeps Click commands at the boundary, internal immutable models and use-case services in the center, and the generated Mistral SDK behind one adapter. Configuration, source resolution, formatting, and storage are independently testable, while command tests inject fake gateways and never access the network.

**Tech Stack:** Python 3.11+, Click 8.4, Rich 15, mistralai 2.5, TOML (`tomllib` and `tomli-w`), pytest 9, Ruff, and Pyright.

---

## File Map

- `pyproject.toml`: package metadata, runtime/dev dependencies, console script, and tool configuration.
- `src/mistral_cli/__init__.py`: installed package version.
- `src/mistral_cli/__main__.py`: `python -m mistral_cli` entry point.
- `src/mistral_cli/cli/main.py`: root Click group, application context, and command registration.
- `src/mistral_cli/cli/config.py`: configuration subcommands.
- `src/mistral_cli/cli/ocr.py`: OCR flags and batch orchestration.
- `src/mistral_cli/cli/transcribe.py`: transcription flags and batch orchestration.
- `src/mistral_cli/config.py`: typed TOML loading, atomic updates, permissions, and API-key resolution.
- `src/mistral_cli/console.py`: stdout/stderr Rich consoles.
- `src/mistral_cli/errors.py`: domain errors, SDK error translation, redaction, and exit behavior.
- `src/mistral_cli/models.py`: internal source, request, output, and result types.
- `src/mistral_cli/sources.py`: local/URL validation, filename handling, and OCR input classification.
- `src/mistral_cli/formatters.py`: deterministic Markdown and JSON-envelope construction.
- `src/mistral_cli/storage.py`: timestamped output naming and atomic no-overwrite writes.
- `src/mistral_cli/mistral_client.py`: official SDK adapter.
- `src/mistral_cli/services/ocr.py`: OCR use case and gateway protocol.
- `src/mistral_cli/services/transcription.py`: transcription use case and gateway protocol.
- `tests/`: boundary-focused unit and Click integration tests.
- `README.md`: user and contributor documentation.

### Task 1: Package Scaffold and Root Command

**Files:**
- Modify: `pyproject.toml`
- Delete: `main.py`
- Create: `src/mistral_cli/__init__.py`
- Create: `src/mistral_cli/__main__.py`
- Create: `src/mistral_cli/cli/__init__.py`
- Create: `src/mistral_cli/cli/main.py`
- Create: `tests/test_cli_root.py`

- [ ] **Step 1: Configure the package and development tools**

Replace `pyproject.toml` with:

```toml
[project]
name = "mistral-cli"
version = "0.1.0"
description = "A modern CLI for Mistral OCR and audio transcription"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "click>=8.4.2,<9",
    "mistralai>=2.5.1,<3",
    "rich>=15.0.0,<16",
    "tomli-w>=1.2.0,<2",
]

[project.scripts]
mistral = "mistral_cli.cli.main:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "pyright>=1.1.411,<2",
    "pytest>=9.1.1,<10",
    "pytest-cov>=7.0.0,<8",
    "ruff>=0.15.20,<0.16",
]

[tool.pytest.ini_options]
addopts = "-ra --strict-config --strict-markers"
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.11"
typeCheckingMode = "strict"
```

Run: `uv sync`

Expected: dependencies resolve and `uv.lock` is created.

- [ ] **Step 2: Write the failing root-command tests**

```python
from click.testing import CliRunner

from mistral_cli.cli.main import cli


def test_root_help_lists_initial_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "config" in result.output
    assert "ocr" in result.output
    assert "transcribe" in result.output


def test_version_reports_package_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output
```

- [ ] **Step 3: Run the tests to verify RED**

Run: `uv run pytest tests/test_cli_root.py -v`

Expected: collection fails because `mistral_cli.cli.main` does not exist.

- [ ] **Step 4: Implement the minimal package and root group**

Create the package modules, expose `__version__` through
`importlib.metadata.version`, define a Click group with `--version`,
`--debug`, and `--config`, and register placeholder `config`, `ocr`, and
`transcribe` groups that have useful one-line help.

The root context is:

```python
@dataclass(frozen=True, slots=True)
class AppContext:
    config_path: Path
    debug: bool
```

Use `Path("~/.mistral/config.toml").expanduser()` as the default without
resolving it, so test paths and symlinks retain their intended semantics.

- [ ] **Step 5: Run root tests to verify GREEN**

Run: `uv run pytest tests/test_cli_root.py -v`

Expected: 2 tests pass.

- [ ] **Step 6: Remove the obsolete top-level `main.py` and commit**

```bash
git add pyproject.toml uv.lock src tests/test_cli_root.py main.py
git commit -m "build: scaffold installable mistral CLI"
```

### Task 2: Secure, Extensible TOML Configuration

**Files:**
- Create: `src/mistral_cli/config.py`
- Replace placeholder: `src/mistral_cli/cli/config.py`
- Create: `tests/test_config.py`
- Create: `tests/test_config_cli.py`

- [ ] **Step 1: Write failing configuration-store tests**

Cover these concrete behaviors:

```python
def test_environment_api_key_takes_precedence(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "file-key"\n')
    monkeypatch.setenv("MISTRAL_API_KEY", "environment-key")

    assert ConfigStore(path).resolve_api_key() == "environment-key"


def test_set_api_key_preserves_other_values_and_secures_paths(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    store = ConfigStore(path)
    store.set("api-key", "secret")

    assert store.load().api_key == "secret"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_malformed_toml_is_not_silently_replaced(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = [valid")

    with pytest.raises(ConfigError, match="Could not parse"):
        ConfigStore(path).load()
```

Also test missing keys, blank keys, unknown config names, unset behavior,
atomic replacement, `config_version`, and API-key redaction.

- [ ] **Step 2: Run configuration-store tests to verify RED**

Run: `uv run pytest tests/test_config.py -v`

Expected: import fails because `mistral_cli.config` does not exist.

- [ ] **Step 3: Implement the configuration store**

Use:

```python
@dataclass(frozen=True, slots=True)
class AppConfig:
    config_version: int = 1
    api_key: str | None = None


class ConfigStore:
    """Owns one versioned TOML configuration file."""
```

Its public methods are `load() -> AppConfig`, `resolve_api_key() -> str`,
`set(name: str, value: str) -> None`, `unset(name: str) -> bool`, and
`redacted() -> Mapping[str, object]`. The constructor accepts `path: Path` and
an optional environment mapping.

Parse with `tomllib.loads` and serialize with `tomli_w.dumps`. Reject unknown
top-level keys so misspellings do not silently become dead configuration.
Create the parent with `0700`; write UTF-8 to a same-directory temporary file,
flush and `fsync`, set `0600`, then `os.replace`. Reapply `0600` after replace.
On missing credentials, raise an error that names both `MISTRAL_API_KEY` and
`mistral config set api-key`.

- [ ] **Step 4: Run configuration-store tests to verify GREEN**

Run: `uv run pytest tests/test_config.py -v`

Expected: all configuration-store tests pass.

- [ ] **Step 5: Write failing configuration-command tests**

Use `CliRunner` with `--config` and cover:

```python
def test_config_set_prompts_without_echoing_secret(tmp_path):
    result = runner.invoke(
        cli,
        ["--config", str(tmp_path / "config.toml"), "config", "set", "api-key"],
        input="secret-value\n",
    )
    assert result.exit_code == 0
    assert "secret-value" not in result.output


def test_config_set_from_stdin(tmp_path):
    result = runner.invoke(
        cli,
        [
            "--config",
            str(tmp_path / "config.toml"),
            "config",
            "set",
            "api-key",
            "--stdin",
        ],
        input="secret-value\n",
    )
    assert result.exit_code == 0


def test_config_show_redacts_api_key(tmp_path):
    path = tmp_path / "config.toml"
    ConfigStore(path).set("api-key", "secret-value")
    result = runner.invoke(cli, ["--config", str(path), "config", "show"])
    assert result.exit_code == 0
    assert "********" in result.output
    assert "secret-value" not in result.output
```

Also test `unset`, `path`, empty stdin, and the mutual exclusion of prompt and
`--stdin`.

- [ ] **Step 6: Run configuration-command tests to verify RED**

Run: `uv run pytest tests/test_config_cli.py -v`

Expected: failures because the placeholder command has no subcommands.

- [ ] **Step 7: Implement `config set/show/unset/path`**

Define nested Click groups so the UX is exactly:

```text
mistral config set api-key [--stdin]
mistral config show
mistral config unset api-key
mistral config path
```

Use `click.prompt("API key", hide_input=True, confirmation_prompt=True)` for the
interactive secret. Read one value from stdin with `click.get_text_stream`,
strip only trailing line endings, and reject an empty value. Never accept the
API key as a positional argument.

- [ ] **Step 8: Run all configuration tests and commit**

Run: `uv run pytest tests/test_config.py tests/test_config_cli.py -v`

Expected: all tests pass.

```bash
git add src/mistral_cli/config.py src/mistral_cli/cli tests/test_config.py tests/test_config_cli.py
git commit -m "feat: add secure TOML configuration"
```

### Task 3: Internal Models and Source Resolution

**Files:**
- Create: `src/mistral_cli/models.py`
- Create: `src/mistral_cli/sources.py`
- Create: `tests/test_sources.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing source-resolution tests**

Test readable local files, missing paths, directories, HTTP(S) URLs, rejected
schemes, percent-decoded URL names, empty URL paths, query strings, control
characters, and common image classification:

```python
def test_url_filename_is_decoded_and_query_is_ignored():
    source = resolve_source("https://example.test/My%20File.pdf?token=x", "document")
    assert source.filename == "My File.pdf"
    assert source.kind is SourceKind.URL


def test_ocr_image_url_is_classified_from_suffix():
    source = resolve_source("https://example.test/scan.PNG", "document")
    assert source.ocr_kind is OcrSourceKind.IMAGE


def test_local_unknown_extension_defaults_to_document(tmp_path):
    path = tmp_path / "input.unknown"
    path.write_bytes(b"document")
    assert resolve_source(str(path), "document").ocr_kind is OcrSourceKind.DOCUMENT
```

- [ ] **Step 2: Run source tests to verify RED**

Run: `uv run pytest tests/test_sources.py -v`

Expected: import fails because source modules do not exist.

- [ ] **Step 3: Implement immutable source and request models**

Create enums and frozen, slotted dataclasses:

```python
class SourceKind(StrEnum):
    FILE = "file"
    URL = "url"


class OcrSourceKind(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class InputSource:
    kind: SourceKind
    value: str
    filename: str
    path: Path | None = None
    ocr_kind: OcrSourceKind = OcrSourceKind.DOCUMENT


@dataclass(frozen=True, slots=True)
class OcrRequest:
    source: InputSource
    model: str
    pages: str | None = None
    table_format: Literal["markdown", "html"] | None = None
    extract_header: bool = False
    extract_footer: bool = False
    include_images: bool = False
    image_limit: int | None = None
    image_min_size: int | None = None
    include_blocks: bool = False
    confidence: Literal["page", "word"] | None = None
    timeout_ms: int = 300_000


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    source: InputSource
    model: str
    language: str | None = None
    temperature: float | None = None
    diarize: bool = False
    context_bias: tuple[str, ...] = ()
    timestamps: tuple[Literal["segment", "word"], ...] = ()
    timeout_ms: int = 300_000
```

Add `OutputFormat`, `ApiResult`, and `SavedResult` types needed by later tasks.

- [ ] **Step 4: Implement source parsing and sanitization**

Use `urllib.parse.urlsplit` and allow only `http` or `https`. For local files,
expand `~`, require `is_file()`, and open once in binary mode to prove
readability. Use `mimetypes.guess_type`; an `image/*` MIME or known image URL
suffix maps to `IMAGE`, otherwise `DOCUMENT`. Sanitize filenames by replacing
`/`, `\`, control characters, and reserved dot-only names while retaining
Unicode.

- [ ] **Step 5: Write model validation tests**

Cover page syntax, nonnegative image controls, their dependency on
`include_images`, at most 100 context-bias values, no blank context values,
finite temperature, positive timeout, language/timestamp incompatibility, and
stable timestamp de-duplication.

- [ ] **Step 6: Run model tests to verify RED**

Run: `uv run pytest tests/test_models.py -v`

Expected: individual validation cases fail until validators are added.

- [ ] **Step 7: Add pure validation constructors**

Provide pure `build_ocr_request` and `build_transcription_request` functions
whose named parameters match their request dataclass fields plus a
`timeout_seconds: float` input. Raise `InputError` with option names in
messages. Accept pages only when every comma-separated token is an integer or
ascending `start-end` range. Convert seconds to integral milliseconds after
checking `math.isfinite` and `timeout > 0`.

- [ ] **Step 8: Run model/source tests and commit**

Run: `uv run pytest tests/test_sources.py tests/test_models.py -v`

Expected: all tests pass.

```bash
git add src/mistral_cli/models.py src/mistral_cli/sources.py tests/test_sources.py tests/test_models.py
git commit -m "feat: add validated sources and request models"
```

### Task 4: Result Formatting and Atomic Persistence

**Files:**
- Create: `src/mistral_cli/formatters.py`
- Create: `src/mistral_cli/storage.py`
- Create: `tests/test_formatters.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing formatter tests**

Use plain dictionaries shaped like current Mistral responses. Assert:

- OCR pages remain ordered and have `<!-- Page N -->` markers.
- Extracted headers and footers are retained once.
- Plain transcription uses `response["text"]`.
- Segment transcription renders `HH:MM:SS.mmm` spans and speaker IDs.
- Missing optional response fields do not crash formatting.
- Provenance never contains the API key or config path.

Example expectation:

```python
assert (
    format_timestamp(65.125)
    == "00:01:05.125"
)
assert "**[00:00:01.000–00:00:02.500] Speaker 1:** Hello" in markdown
```

- [ ] **Step 2: Run formatter tests to verify RED**

Run: `uv run pytest tests/test_formatters.py -v`

Expected: import fails because `formatters.py` does not exist.

- [ ] **Step 3: Implement deterministic Markdown and envelope formatting**

Implement four public functions: `format_ocr_markdown(result: ApiResult) ->
str`, `format_transcription_markdown(result: ApiResult) -> str`,
`build_envelope(result: ApiResult, cli_version: str) -> dict[str, object]`,
and `format_timestamp(seconds: float) -> str`.

Use simple Markdown and HTML comments rather than Rich rendering. Serialize the
envelope with `json.dumps(envelope, ensure_ascii=False, indent=2) + "\n"`.

- [ ] **Step 4: Run formatter tests to verify GREEN**

Run: `uv run pytest tests/test_formatters.py -v`

Expected: all formatter tests pass.

- [ ] **Step 5: Write failing storage tests**

Inject a fixed UTC clock and assert exact paths:

```python
assert saved.markdown.name == "20260702T123456.123456Z-input.pdf.md"
assert saved.json.name == "20260702T123456.123456Z-input.pdf.json"
```

Test the default OCR/transcription directories, custom directories, each output
format, Unicode filenames, collision suffixes, no overwrite, UTF-8, valid JSON,
and cleanup of temporary files when linking fails.

- [ ] **Step 6: Run storage tests to verify RED**

Run: `uv run pytest tests/test_storage.py -v`

Expected: import fails because `storage.py` does not exist.

- [ ] **Step 7: Implement atomic no-overwrite storage**

Create `ResultStore(base_dir: Path | None = None, clock:
Callable[[], datetime] = utc_now)`. Its `save` method accepts `result:
ApiResult`, `markdown: str`, `output_format: OutputFormat`, and `output_dir:
Path | None`, and returns `SavedResult`.

Select `~/.mistral/ocr` or `~/.mistral/transcriptions` when no explicit output
directory is given. Write a complete same-directory temp file, flush and
`fsync`, then publish it with `os.link(temp, destination)` so an existing path
causes `FileExistsError` instead of replacement. Remove the temp link in
`finally`. On a base-name collision, select `timestamp-1-filename`,
`timestamp-2-filename`, and so on for the whole requested output set.

- [ ] **Step 8: Run formatter/storage tests and commit**

Run: `uv run pytest tests/test_formatters.py tests/test_storage.py -v`

Expected: all tests pass.

```bash
git add src/mistral_cli/formatters.py src/mistral_cli/storage.py tests/test_formatters.py tests/test_storage.py
git commit -m "feat: persist Markdown and JSON results atomically"
```

### Task 5: Mistral SDK Adapter and Use-Case Services

**Files:**
- Create: `src/mistral_cli/services/__init__.py`
- Create: `src/mistral_cli/services/ocr.py`
- Create: `src/mistral_cli/services/transcription.py`
- Create: `src/mistral_cli/mistral_client.py`
- Create: `tests/test_mistral_client.py`
- Create: `tests/test_services.py`

- [ ] **Step 1: Write failing adapter request-mapping tests**

Inject a fake SDK client factory. Assert exact current SDK keyword mappings:

```python
gateway.ocr(request)
sdk.ocr.process.assert_called_once_with(
    model="mistral-ocr-latest",
    document={
        "type": "document_url",
        "document_url": "data:application/pdf;base64,cGRm",
    },
    pages="0,2-4",
    table_format="markdown",
    extract_header=True,
    extract_footer=False,
    include_image_base64=True,
    image_limit=10,
    image_min_size=32,
    include_blocks=True,
    confidence_scores_granularity="word",
    timeout_ms=30_000,
)
```

Also assert `image_url`, URL OCR, local transcription stream/file name, URL
transcription, omission of unset optionals, and `model_dump(mode="json",
exclude_unset=True)`.

- [ ] **Step 2: Run adapter tests to verify RED**

Run: `uv run pytest tests/test_mistral_client.py -v`

Expected: import fails because the adapter does not exist.

- [ ] **Step 3: Implement the official SDK adapter**

Create a `MistralGateway` that accepts an API key and optional
`client_factory`. Import `Mistral` only in this module. For local OCR, read
bytes, infer a MIME with fallback `application/octet-stream`, and construct a
base64 data URL. For local transcription, keep the binary file open only for
the duration of `audio.transcriptions.complete`.

Build kwargs dictionaries and omit values that are semantically unset rather
than passing `None`. Call:

```python
client.ocr.process(**kwargs)
client.audio.transcriptions.complete(**kwargs)
```

Use the SDK as a context manager and return plain dictionaries.

- [ ] **Step 4: Run adapter tests to verify GREEN**

Run: `uv run pytest tests/test_mistral_client.py -v`

Expected: all request-mapping tests pass.

- [ ] **Step 5: Write failing service tests**

Define gateway protocols and verify each service creates `ApiResult` with the
source, sanitized request metadata, operation name, response, and one injected
creation timestamp. Verify gateway exceptions are not swallowed.

- [ ] **Step 6: Run service tests to verify RED**

Run: `uv run pytest tests/test_services.py -v`

Expected: failures because service classes do not exist.

- [ ] **Step 7: Implement focused OCR and transcription services**

Each service accepts its protocol in `__init__` and has one `run(request)`
method. Request metadata must contain only JSON-safe non-secret values. Keep
SDK types out of protocol signatures.

- [ ] **Step 8: Run adapter/service tests and commit**

Run: `uv run pytest tests/test_mistral_client.py tests/test_services.py -v`

Expected: all tests pass.

```bash
git add src/mistral_cli/mistral_client.py src/mistral_cli/services tests/test_mistral_client.py tests/test_services.py
git commit -m "feat: integrate Mistral OCR and transcription SDK"
```

### Task 6: Error Translation and Rich Console Boundary

**Files:**
- Create: `src/mistral_cli/errors.py`
- Create: `src/mistral_cli/console.py`
- Create: `tests/test_errors.py`
- Create: `tests/test_console.py`

- [ ] **Step 1: Write failing redaction/error tests**

Test secret replacement, status-specific messages for 401/403, 429, 422, 5xx,
timeouts, connection failures, and unknown errors. Assert normal mode has no
traceback or response body and debug mode still redacts the API key.

- [ ] **Step 2: Run error tests to verify RED**

Run: `uv run pytest tests/test_errors.py -v`

Expected: import fails because error types do not exist.

- [ ] **Step 3: Implement domain errors and SDK translation**

Define `MistralCliError`, `ConfigError`, `InputError`, `PersistenceError`, and
`ApiError`. Inspect SDK exceptions without importing private generated types:
read public `status_code`, `message`, and `body` attributes defensively. Map
known statuses to actionable messages. Implement `redact(text: str, secrets:
Iterable[str]) -> str` and `translate_exception(error: Exception) ->
MistralCliError`.

- [ ] **Step 4: Run error tests to verify GREEN**

Run: `uv run pytest tests/test_errors.py -v`

Expected: all error tests pass.

- [ ] **Step 5: Write and implement console tests**

Create one normal Rich `Console` for stdout and one `Console(stderr=True)` for
status/errors. Test with `StringIO` that status output never reaches stdout and
nonterminal output contains no ANSI sequences. Expose a small `ConsoleBundle`
that commands can inject.

- [ ] **Step 6: Run boundary tests and commit**

Run: `uv run pytest tests/test_errors.py tests/test_console.py -v`

Expected: all tests pass.

```bash
git add src/mistral_cli/errors.py src/mistral_cli/console.py tests/test_errors.py tests/test_console.py
git commit -m "feat: add safe errors and terminal output"
```

### Task 7: OCR Command

**Files:**
- Replace placeholder: `src/mistral_cli/cli/ocr.py`
- Modify: `src/mistral_cli/cli/main.py`
- Create: `tests/test_ocr_cli.py`

- [ ] **Step 1: Write failing OCR help and request tests**

Assert every approved flag is documented. Invoke a local PDF through an
injected fake OCR gateway and fixed clock, then assert request values, both
saved files, success output, and valid JSON envelope. Add URL and `--stdout`
cases.

- [ ] **Step 2: Run OCR command tests to verify RED**

Run: `uv run pytest tests/test_ocr_cli.py -v`

Expected: failures because the placeholder command lacks options and behavior.

- [ ] **Step 3: Implement OCR options and orchestration**

Use Click `Choice` types and typed paths for output/config, but leave `SOURCE`
as strings so URLs remain valid. For each source:

1. resolve and validate it;
2. resolve the API key;
3. build the internal request;
4. show an indeterminate Rich status on stderr;
5. call `OcrService`;
6. format and persist the result;
7. print saved paths;
8. print Markdown to stdout only when requested.

Construct the gateway once per command after API-key resolution. Expose
internal context factories so tests inject fakes without monkeypatching the
generated SDK.

- [ ] **Step 4: Add failing OCR validation and batch tests**

Cover malformed pages, negative image controls, image controls without
`--include-images`, zero timeout, unreadable files, unsupported URLs, one
failure among three sources, and interrupted processing.

- [ ] **Step 5: Implement partial-failure behavior**

Catch expected per-source errors, translate them, print
`<source>: <message>` to stderr, and continue. At completion print counts and
raise `click.exceptions.Exit(1)` when any source failed. Let Click's
`Abort`/keyboard interruption retain conventional behavior.

- [ ] **Step 6: Run OCR tests and commit**

Run: `uv run pytest tests/test_ocr_cli.py -v`

Expected: all OCR command tests pass.

```bash
git add src/mistral_cli/cli/main.py src/mistral_cli/cli/ocr.py tests/test_ocr_cli.py
git commit -m "feat: add OCR command"
```

### Task 8: Transcription Command

**Files:**
- Replace placeholder: `src/mistral_cli/cli/transcribe.py`
- Modify: `src/mistral_cli/cli/main.py`
- Create: `tests/test_transcribe_cli.py`

- [ ] **Step 1: Write failing transcription help and request tests**

Assert every approved flag appears in help. Invoke a local audio file through
an injected fake gateway and fixed clock, then assert local stream mapping,
default model, both outputs, and readable Markdown. Add URL, diarization,
context bias, timestamps, and `--stdout` cases.

- [ ] **Step 2: Run transcription tests to verify RED**

Run: `uv run pytest tests/test_transcribe_cli.py -v`

Expected: failures because the placeholder command lacks behavior.

- [ ] **Step 3: Implement transcription options and orchestration**

Mirror the OCR orchestration while building `TranscriptionRequest`. Define
repeatable Click options:

```python
@click.option("--context-bias", multiple=True, metavar="TEXT")
@click.option(
    "--timestamps",
    multiple=True,
    type=click.Choice(["segment", "word"], case_sensitive=False),
)
```

Persist full JSON and segment-aware Markdown using the shared store.

- [ ] **Step 4: Add failing validation and partial-batch tests**

Cover language/timestamps conflict, 101 context values, blank context values,
NaN/infinite temperature, zero timeout, invalid URLs, a failed middle source,
and speaker/timestamp Markdown.

- [ ] **Step 5: Implement validation and summary behavior**

Use the pure request builder from Task 3 and the same per-source error
translation/summary contract as OCR.

- [ ] **Step 6: Run transcription tests and commit**

Run: `uv run pytest tests/test_transcribe_cli.py -v`

Expected: all transcription command tests pass.

```bash
git add src/mistral_cli/cli/main.py src/mistral_cli/cli/transcribe.py tests/test_transcribe_cli.py
git commit -m "feat: add transcription command"
```

### Task 9: Documentation, Packaging, and Full Verification

**Files:**
- Replace: `README.md`
- Modify: `.gitignore`
- Modify as required: source/tests found by verification

- [ ] **Step 1: Write the README**

Document:

- `uv tool install .` and `pipx install .`
- hidden-prompt and stdin configuration
- `MISTRAL_API_KEY` precedence
- local and URL OCR examples
- OCR page/table/header/footer/image/block/confidence options
- local and URL transcription examples
- language, diarization, context bias, temperature, and timestamp options
- the documented language/timestamps incompatibility
- output directories, filenames, Markdown/JSON behavior, and `--stdout`
- config/output security behavior
- development setup and all quality commands
- direct links to official Mistral OCR, transcription, Click, and Rich docs

- [ ] **Step 2: Update ignores and inspect package contents**

Ignore `.coverage`, `.pytest_cache/`, `.ruff_cache/`, `.pyright/`, and
`htmlcov/`. Run:

```bash
uv build
unzip -l dist/*.whl
```

Expected: the wheel contains `mistral_cli` modules and metadata, with no tests,
temporary files, or local configuration.

- [ ] **Step 3: Run the complete test suite**

Run: `uv run pytest --cov=mistral_cli --cov-report=term-missing`

Expected: all tests pass with no unexpected warnings.

- [ ] **Step 4: Run lint and formatting checks**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: both commands exit 0. If formatting changes are needed, run
`uv run ruff format .`, inspect the diff, and repeat both checks.

- [ ] **Step 5: Run strict type checking**

Run: `uv run pyright`

Expected: 0 errors.

- [ ] **Step 6: Run installed CLI smoke checks**

Run:

```bash
uv run mistral --version
uv run mistral --help
uv run mistral config --help
uv run mistral ocr --help
uv run mistral transcribe --help
```

Expected: every command exits 0, the version is `0.1.0`, and help reflects the
documented command surface.

- [ ] **Step 7: Review requirements and repository diff**

Compare every section of
`docs/superpowers/specs/2026-07-02-ocr-transcription-cli-design.md` against the
implementation. Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only intentional project files are changed.

- [ ] **Step 8: Commit the completed documentation and verification fixes**

```bash
git add README.md .gitignore pyproject.toml uv.lock src tests
git commit -m "docs: document Mistral CLI workflows"
```
