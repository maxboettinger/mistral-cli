# Agentic CLI Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--json` NDJSON stdout, stable exit codes, a `mistral agent` discovery command, and `--quiet`/`--no-save`/`--dry-run` ergonomics, per `docs/superpowers/specs/2026-07-03-agentic-cli-design.md`.

**Architecture:** New record builders in `formatters.py` + a JSON Schema in `schema.py` define the stdout contract; a new shared batch runner (`cli/runner.py`) replaces the duplicated loops in `cli/ocr.py`/`cli/transcribe.py` and owns all new flag behavior; `cli/agent.py` serves a packaged guide + schema.

**Tech Stack:** click, pytest + CliRunner, hatchling packaging, pyright strict, ruff.

## Global Constraints

- Python floor 3.11 (`requires-python = ">=3.11"`): no PEP 695 generics; use `TypeVar`/`Generic`.
- pyright strict includes `tests/`; every module starts with `from __future__ import annotations`; frozen slotted dataclasses.
- Security invariant: anything reaching stdout/stderr goes through `ConsoleBundle.write_stdout/write_stderr` (sanitizes) and is redacted first. NDJSON is serialized with `ensure_ascii=True` so sanitization is a no-op on it.
- stdout carries only results (Markdown via `--stdout`, NDJSON via `--json`); everything else stderr.
- Tests monkeypatch `mistral_cli.cli.ocr.create_gateway` / `create_result_store` (same for transcribe) — these module-level seams must survive the refactor and be referenced late-bound (inside lambdas) by the runner plan.
- Exit codes: 0 all ok, 1 ≥1 source failed, 2 usage (click default), 3 setup/auth/config.
- Commit after each task; run `uv run pytest`, `uv run ruff check .`, `uv run pyright` before each commit.

---

### Task 1: Exit-code constants and error codes (`errors.py`)

**Files:**
- Modify: `src/mistral_cli/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `EXIT_FAILURE = 1`, `EXIT_USAGE = 2`, `EXIT_SETUP = 3` (module constants); `error_code(error: MistralCliError) -> str` returning `"input_error" | "config_error" | "api_error" | "persistence_error" | "unexpected_error"`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_errors.py`)

```python
def test_exit_code_constants_are_stable() -> None:
    assert errors.EXIT_FAILURE == 1
    assert errors.EXIT_USAGE == 2
    assert errors.EXIT_SETUP == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (errors.InputError("x"), "input_error"),
        (errors.ConfigError("x"), "config_error"),
        (errors.ApiError("x", status_code=429), "api_error"),
        (errors.PersistenceError("x"), "persistence_error"),
        (errors.MistralCliError("x"), "unexpected_error"),
    ],
)
def test_error_code_maps_taxonomy(
    error: errors.MistralCliError, expected: str
) -> None:
    assert errors.error_code(error) == expected
```

(Match the file's existing import style — check whether it imports `errors` as a module or names directly and follow it.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_errors.py -q`; expect AttributeError/NameError failures.
- [ ] **Step 3: Implement** in `errors.py`:

```python
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_SETUP = 3


def error_code(error: MistralCliError) -> str:
    """Return the stable machine-readable code for a translated error."""
    if isinstance(error, InputError):
        return "input_error"
    if isinstance(error, ConfigError):
        return "config_error"
    if isinstance(error, ApiError):
        return "api_error"
    if isinstance(error, PersistenceError):
        return "persistence_error"
    return "unexpected_error"
```

- [ ] **Step 4: Verify pass**, then lint/typecheck.
- [ ] **Step 5: Commit** — `feat: add stable exit codes and error-code taxonomy`

---

### Task 2: Request-metadata builders (`models.py`), services delegate

**Files:**
- Modify: `src/mistral_cli/models.py`, `src/mistral_cli/services/ocr.py`, `src/mistral_cli/services/transcription.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `ocr_request_metadata(request: OcrRequest) -> dict[str, JSONValue]`; `transcription_request_metadata(request: TranscriptionRequest) -> dict[str, JSONValue]`. Dict contents identical to what the services currently inline (keys: model, pages, table_format, extract_header, extract_footer, include_images, image_limit, image_min_size, include_blocks, confidence, timeout_ms for OCR; model, language, temperature, diarize, context_bias (as list), timestamps (as list), timeout_ms for transcription — copy exactly from the current service code).

- [ ] **Step 1: Failing test** asserting `ocr_request_metadata(build_ocr_request(...))` returns the exact dict, and same for transcription.
- [ ] **Step 2: Verify fail.**
- [ ] **Step 3: Implement** the two functions in `models.py` by moving the dict literals out of `OcrService.run` / `TranscriptionService.run`; services call the new functions (`request_metadata=ocr_request_metadata(request)`).
- [ ] **Step 4: Full suite passes** (service tests prove no behavior change).
- [ ] **Step 5: Commit** — `refactor: extract request metadata builders from services`

---

### Task 3: NDJSON record builders (`formatters.py`)

**Files:**
- Modify: `src/mistral_cli/formatters.py`
- Test: `tests/test_formatters.py`

**Interfaces:**
- Produces:
  - `serialize_ndjson(value: object) -> str` — compact, `ensure_ascii=True`, `allow_nan=False`, trailing `"\n"`.
  - `RECORD_SCHEMA_VERSION = 1`
  - `build_ok_record(*, source: str, envelope: dict[str, JSONValue], saved_markdown: str | None, saved_json: str | None) -> dict[str, JSONValue]`
  - `build_error_record(*, source: str | None, code: str, message: str, status_code: int | None) -> dict[str, JSONValue]`
  - `build_dry_run_record(*, source: str, request_metadata: JSONMapping) -> dict[str, JSONValue]` (passes metadata through `_plain_json(..., omit_sensitive=True)`)
  - `build_summary_record(*, succeeded: int, failed: int) -> dict[str, JSONValue]`

- [ ] **Step 1: Failing tests** covering: each builder's exact dict shape (per the spec's record shapes); `serialize_ndjson` output is single-line, ASCII-only for non-ASCII/control-char input, parses back with `json.loads`, and `sanitize_terminal_text(serialize_ndjson(payload)) == serialize_ndjson(payload)` for a payload containing `"\x1b[31m"` and `"\x9b"` (the ASCII-escape invariant).
- [ ] **Step 2: Verify fail.**
- [ ] **Step 3: Implement:**

```python
RECORD_SCHEMA_VERSION = 1


def serialize_ndjson(value: object) -> str:
    """Serialize one ASCII-only NDJSON record line."""
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        + "\n"
    )


def build_ok_record(
    *,
    source: str,
    envelope: dict[str, JSONValue],
    saved_markdown: str | None,
    saved_json: str | None,
) -> dict[str, JSONValue]:
    """Build the stdout record for a successfully processed source."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "ok",
        "source": source,
        "envelope": envelope,
        "saved": {"markdown": saved_markdown, "json": saved_json},
    }


def build_error_record(
    *,
    source: str | None,
    code: str,
    message: str,
    status_code: int | None,
) -> dict[str, JSONValue]:
    """Build the stdout record for a failed source or setup failure."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "error",
        "source": source,
        "error": {"code": code, "message": message, "status_code": status_code},
    }


def build_dry_run_record(
    *,
    source: str,
    request_metadata: JSONMapping,
) -> dict[str, JSONValue]:
    """Build the stdout record for a validated source under --dry-run."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "dry_run",
        "source": source,
        "request": _plain_json(request_metadata, omit_sensitive=True),
    }


def build_summary_record(*, succeeded: int, failed: int) -> dict[str, JSONValue]:
    """Build the final stdout record of a completed run."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "status": "summary",
        "succeeded": succeeded,
        "failed": failed,
    }
```

- [ ] **Step 4: Pass, lint, typecheck.**
- [ ] **Step 5: Commit** — `feat: add NDJSON record builders for --json output`

---

### Task 4: Output schema (`schema.py`)

**Files:**
- Create: `src/mistral_cli/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `record_schema() -> dict[str, JSONValue]` — a JSON Schema (draft 2020-12) with `oneOf` over the four record variants; envelope described with its five required keys (`schema_version`, `created_at`, `source`, `request`, `response`, `cli_version` — six keys) and `request`/`response` left as open objects.

- [ ] **Step 1: Failing tests:** `record_schema()` declares `$schema == "https://json-schema.org/draft/2020-12/schema"`; each record produced by the Task 3 builders contains exactly the `required` keys of the matching `oneOf` variant (walk variants by `properties.status.const`).
- [ ] **Step 2–3: Implement** — a module holding one function returning a literal dict. Variant sketch (repeat the pattern for all four statuses; the ok variant's `envelope` property lists the six envelope keys as `required` with `"type": "object"` for request/response, and `saved` requires `markdown`/`json` with `"type": ["string", "null"]`):

```python
def record_schema() -> dict[str, JSONValue]:
    """Return the JSON Schema for one NDJSON stdout record."""
    ...  # literal dict per above; no runtime construction logic
```

- [ ] **Step 4: Pass, lint, typecheck.**
- [ ] **Step 5: Commit** — `feat: add JSON Schema for NDJSON output records`

---

### Task 5: Shared batch runner (`cli/runner.py`) + rewire both commands

**Files:**
- Create: `src/mistral_cli/cli/runner.py`
- Modify: `src/mistral_cli/cli/common.py` (add `write_stdout` to `CommandConsoles`; delete `resolve_api_key`), `src/mistral_cli/cli/ocr.py`, `src/mistral_cli/cli/transcribe.py`
- Test: existing `tests/test_ocr_cli.py`, `tests/test_transcribe_cli.py` (setup-error exit-code assertions change 1 → 3), `tests/test_cli_common.py` (drop `resolve_api_key` test)

**Interfaces:**
- Produces:

```python
RequestT = TypeVar("RequestT")
RequestT_contra = TypeVar("RequestT_contra", contravariant=True)


class BatchService(Protocol[RequestT_contra]):
    def run(self, request: RequestT_contra) -> ApiResult: ...


@dataclass(frozen=True, slots=True)
class OutputOptions:
    output_format: OutputFormat
    output_dir: Path | None
    write_markdown_stdout: bool
    write_json_stdout: bool
    quiet: bool
    no_save: bool
    dry_run: bool


@dataclass(frozen=True, slots=True)
class BatchPlan(Generic[RequestT]):
    setup_debug_context: str
    source_debug_prefix: str
    build_request: Callable[[str], RequestT]
    request_metadata: Callable[[RequestT], dict[str, JSONValue]]
    create_service: Callable[[str], BatchService[RequestT]]
    create_store: Callable[[], ResultStore]
    format_markdown: Callable[[ApiResult], str]


def run_batch(
    context: AppContext,
    sources: tuple[str, ...],
    plan: BatchPlan[RequestT],
    options: OutputOptions,
) -> None: ...
```

- Consumes: Tasks 1–3 (`EXIT_*`, `error_code`, record builders, metadata builders).

**Runner semantics (implement exactly):**
1. Validate options first: `write_json_stdout and write_markdown_stdout` → `click.UsageError("--json cannot be combined with --stdout.")`; `no_save and not (json or stdout)` → `UsageError("--no-save requires --json or --stdout.")`; `no_save and output_dir` → `UsageError("--no-save cannot be combined with --output-dir.")`.
2. Per source: `plan.build_request(source_value)`; on exception report (stderr via `report_error`, plus error record when `--json`), `failures += 1`, continue.
3. `--dry-run`: after building the request, emit `Would process: {source} (model {metadata["model"]})` to stderr (unless `--quiet`, through `safe_terminal_text`) or a `dry_run` record when `--json`; `successes += 1`; never create the runtime (no API key needed), never call the gateway or store.
4. Lazy runtime creation on first non-dry source: resolve key via `ConfigStore(context.config_path).resolve_api_key()`; on `ConfigError` report (stderr `Setup error:` + error record with `source: null` when `--json`) and `raise click.exceptions.Exit(EXIT_SETUP)`. Same for service/store construction failure. Extend secrets with the resolved key (`extend_secrets`).
5. Success path mirrors the current loop (Processing line → run → `redact_result` → `format_markdown` → `safe_terminal_text` → save unless `--no-save` (then `SavedResult()`) ) with `Processing:`/`Saved:` lines gated on `not quiet`.
6. `--json` ok record: `build_envelope(safe_result, get_cli_version())`, `build_ok_record(source=redact(source_value, secrets), envelope=..., saved_markdown=redact(str(path), secrets) or None, ...)`, emitted via `context.consoles.write_stdout(serialize_ndjson(record))`. Wrap serialization in `try/except (TypeError, ValueError)` → report as failure, continue (don't count success).
7. Markdown stdout unchanged (`\n\n---\n\n` separator between documents).
8. End: `Summary: N succeeded, M failed.` to stderr unless `--quiet`; summary record when `--json`; `Exit(EXIT_FAILURE)` if any failures.
9. Error records: `translated = translate_exception(error)`; `code=error_code(translated)`; `message=redact(str(translated), secrets)`; `status_code=translated.status_code if isinstance(translated, ApiError) else None`.

**Command rewiring:** `ocr.py`/`transcribe.py` keep their click decorators, normalization (`table_format`/`confidence` → None, `OutputFormat(...)`), the `create_gateway`/`create_result_store` seams, and delegate to `run_batch` with a `BatchPlan` whose `create_service`/`create_store` are lambdas (`lambda api_key: OcrService(create_gateway(api_key))`, `lambda: create_result_store()`) so monkeypatched seams stay effective. Do **not** add the new click options yet — pass `write_json_stdout=False, quiet=False, no_save=False, dry_run=False` in this task so the diff is a pure refactor plus the exit-3 change.

- [ ] **Step 1:** Update setup-error tests (search both CLI test files for missing-API-key/setup scenarios asserting `exit_code == 1`; change to `== 3` and import/assert against `errors.EXIT_SETUP`). Run: they fail (still exit 1).
- [ ] **Step 2:** Implement `runner.py`; rewire both commands; delete `common.resolve_api_key` and its test (`test_resolve_api_key_reports_safe_setup_error`); add `write_stdout` to the `CommandConsoles` protocol.
- [ ] **Step 3:** Full suite passes; lint; typecheck.
- [ ] **Step 4: Commit** — `refactor: extract shared batch runner; setup failures exit 3`

---

### Task 6: New flags `--json`, `--quiet`, `--no-save`, `--dry-run` on both commands

**Files:**
- Modify: `src/mistral_cli/cli/ocr.py`, `src/mistral_cli/cli/transcribe.py`
- Test: `tests/test_ocr_cli.py`, `tests/test_transcribe_cli.py`

**Interfaces:**
- Consumes: Task 5 `OutputOptions` fields; the click options map 1:1 (`--json` → `write_json_stdout`, `--stdout` → `write_markdown_stdout`).

Click option declarations (identical on both commands):

```python
@click.option(
    "--json",
    "write_json",
    is_flag=True,
    help="Write NDJSON result records to standard output (one per source).",
)
@click.option("--quiet", is_flag=True, help="Suppress progress and summary output.")
@click.option(
    "--no-save",
    is_flag=True,
    help="Do not save result files (requires --json or --stdout).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate sources and options without calling the API.",
)
```

- [ ] **Step 1: Failing tests** (per command file, using the existing fake-gateway fixtures/patterns in each test file):
  - `--json` with two sources: stdout parses as NDJSON; records are `ok, ok, summary` with correct `envelope.schema_version == 1`, `saved` paths existing on disk, summary counts; exit 0.
  - Mixed batch (one bad source): records `error` (with `code == "input_error"`), `ok`, `summary {succeeded:1, failed:1}`; exit 1.
  - Missing API key with `--json`: single `error` record with `source: null`, `code == "config_error"`; exit 3.
  - `--json --stdout` → exit 2, "cannot be combined" in output.
  - `--quiet`: no `Processing:`/`Saved:`/`Summary:` on stderr; error lines still appear on failure.
  - `--no-save --json`: no files created; ok record `saved` both null. `--no-save` alone → exit 2. `--no-save --output-dir X --json` → exit 2.
  - `--dry-run`: gateway never called (fake gateway raises if called / records calls), no files, no API key needed (unset env, empty config); stderr `Would process:` lines; with `--json`, `dry_run` records + summary; exit 0; invalid source under `--dry-run` → error + exit 1.
  - Control-char injection: fake gateway response containing `"\x1b]0;evil\x07"` and `"\x9b31m"` → `--json` stdout still parses and content survives (escaped), assert `json.loads` round-trip contains the original characters.
- [ ] **Step 2: Verify failures** (unknown option errors).
- [ ] **Step 3: Implement** — add the four options + parameters to both command functions, build `OutputOptions` from them. No other logic (runner already implements behavior).
- [ ] **Step 4: Full suite passes; lint; typecheck.**
- [ ] **Step 5: Commit** — `feat: add --json, --quiet, --no-save, --dry-run to ocr and transcribe`

---

### Task 7: `mistral agent` command + packaged guide

**Files:**
- Create: `src/mistral_cli/cli/agent.py`, `src/mistral_cli/data/__init__.py` (empty, so the dir ships in the wheel), `src/mistral_cli/data/agent_guide.md`
- Modify: `src/mistral_cli/cli/main.py`
- Test: `tests/test_agent_cli.py`

**Interfaces:**
- Produces: `agent` click command registered on the root group.

```python
@click.command()
@click.option(
    "--schema",
    "show_schema",
    is_flag=True,
    help="Print the JSON Schema describing --json output records.",
)
@click.pass_obj
def agent(context: AppContext, show_schema: bool) -> None:
    """Print agent-oriented usage documentation for this CLI."""
    if show_schema:
        context.consoles.write_stdout(serialize_json(record_schema()))
        return
    guide = (
        importlib.resources.files("mistral_cli")
        .joinpath("data/agent_guide.md")
        .read_text(encoding="utf-8")
    )
    context.consoles.write_stdout(guide)
```

- Root group gets `epilog="Run 'mistral agent' for agent-oriented usage docs and 'mistral agent --schema' for the JSON output schema."` and `cli.add_command(agent)`.

**Guide content** (`agent_guide.md`, keep under ~120 lines): condensed from the `using-mistral-cli` skill and updated — the two output facts (files always saved unless `--no-save`; stdout/stderr split), setup (`MISTRAL_API_KEY` / `mistral config set api-key --stdin`; never a CLI arg), command tables for ocr/transcribe with the new flags, NDJSON record shapes (copy the four spec examples), error-code table, exit-code table, three jq recipes (`jq -r 'select(.status=="ok") | .envelope.response.text'`, select errors, read summary), and `--dry-run` needing no key.

- [ ] **Step 1: Failing tests:** `mistral agent` exits 0, stdout non-empty, contains `--json`, `exit code`, `schema_version`; `mistral agent --schema` exits 0 and `json.loads(stdout)` has `$schema` and `oneOf`; `mistral --help` output mentions `mistral agent`.
- [ ] **Step 2–3: Implement**; also verify packaging: `uv build && unzip -l dist/*.whl | grep agent_guide` shows the data file.
- [ ] **Step 4: Pass, lint, typecheck.**
- [ ] **Step 5: Commit** — `feat: add mistral agent discovery command with packaged guide and schema`

---

### Task 8: Documentation and skill update

**Files:**
- Modify: `README.md` (new flags, exit codes, `mistral agent`, NDJSON contract), `CLAUDE.md` (runner module in architecture table, new invariant notes: NDJSON ASCII-only, exit codes), `docs/superpowers/specs/2026-07-03-agentic-cli-design.md` (clarify: summary record is the final record of a *completed* run; setup aborts emit only the error record), `~/.claude/skills/using-mistral-cli/SKILL.md` (teach `--json` NDJSON as the preferred programmatic path, new flags, exit codes, `mistral agent`)
- Test: none (docs)

- [ ] **Step 1:** Update the four documents.
- [ ] **Step 2: Commit repo docs** — `docs: document agentic output contract and flags` (skill file is outside the repo; edit in place, no commit).

---

### Task 9: Final verification

- [ ] `uv run pytest --cov=mistral_cli --cov-report=term-missing` — all pass; new modules covered.
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pyright` — zero errors, strict.
- [ ] `uv lock --check && uv build`
- [ ] Smoke: `uv run mistral agent | head`, `uv run mistral agent --schema | jq .`, `MISTRAL_API_KEY= uv run mistral ocr --dry-run --json somefile.pdf` (with a real local file) → dry_run + summary records, exit 0; `uv run mistral ocr --json --stdout x.pdf` → exit 2.
