# Publish-Ready Polish (0.3.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mistral-cli publishable and featurable: MIT license + full packaging metadata, CI, `-h`, SDK retries on by default, drop the unused rich dependency, rewrite `storage.py` at ~1/4 the size, plus small hardening fixes — version 0.3.0.

**Architecture:** No layering changes. New behavior (retries, size guard) flows through the existing seams: CLI option → `build_*_request` validator → frozen request dataclass → `MistralGateway`. Refactors (`console.py`, `storage.py`) preserve their public surfaces exactly so `runner.py` and the services never change.

**Tech Stack:** Python ≥3.11, click 8.4, mistralai 2.6 (`mistralai.client`), hatchling (PEP 639), uv, pytest, ruff, pyright strict, GitHub Actions with `astral-sh/setup-uv`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-publish-polish-design.md`.
- All six gates must pass at every commit: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv lock --check`, `uv build`.
- Every source module keeps `from __future__ import annotations`; pyright runs in strict mode and covers `tests/`.
- SDK imports stay confined to `src/mistral_cli/mistral_client.py`.
- All user-facing strings still pass through `safe_terminal_text()` / `sanitize_terminal_text()`; stdout carries only results (Markdown or NDJSON), everything else goes to stderr.
- The NDJSON record shapes keep `schema_version` 1 — the only contract change is the additive `"retries"` key inside envelope `request` objects.
- `request_fingerprint()` must produce **identical hashes to 0.2.0** for equivalent requests (existing dedupe indexes keep matching).
- Public repo URL: `https://github.com/maxboettinger/mistral-cli`.
- Conventional commit messages (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `ci:`).
- After code edits run `uv run ruff format .` before committing (the repo is format-checked).

---

### Task 1: License, packaging metadata, version 0.3.0

**Files:**
- Modify: `README.md` (commit the pending uncommitted change only)
- Create: `LICENSE`
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerated)

**Interfaces:**
- Produces: version string `0.3.0` (later tasks' clean-install checks assert it); `LICENSE` file referenced by Task 9's CHANGELOG and Task 10's README badges.

- [ ] **Step 1: Commit the pre-existing README change**

The working tree starts with an uncommitted README edit (hardened install instructions) that predates this plan. Commit it as-is so the tree is clean:

```bash
git add README.md
git commit -m "docs: harden install instructions against the PyPI mistral package"
```

- [ ] **Step 2: Create LICENSE**

Create `LICENSE` with exactly:

```text
MIT License

Copyright (c) 2026 Max Boettinger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Update pyproject metadata**

In `pyproject.toml`, replace the `[project]` table (keep `dependencies` unchanged for now — rich is removed in Task 7) and add `[project.urls]` and the wheel `exclude`:

```toml
[project]
name = "mistral-cli"
version = "0.3.0"
description = "A modern CLI for Mistral OCR and audio transcription"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Max Boettinger" }]
requires-python = ">=3.11"
keywords = ["mistral", "ocr", "transcription", "speech-to-text", "cli", "markdown", "pdf"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Text Processing :: General",
]
dependencies = [
    "click>=8.4.2,<9",
    "httpx>=0.28.1,<1",
    "mistralai>=2.5.1,<3",
    "rich>=15.0.0,<16",
    "tomli-w>=1.2.0,<2",
]

[project.urls]
Repository = "https://github.com/maxboettinger/mistral-cli"
Issues = "https://github.com/maxboettinger/mistral-cli/issues"
Changelog = "https://github.com/maxboettinger/mistral-cli/blob/main/CHANGELOG.md"
```

And extend the wheel target so the internal nori dev docs stop shipping:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/mistral_cli"]
exclude = ["**/docs.md"]
```

- [ ] **Step 4: Relock and verify the build**

```bash
uv lock && uv sync
uv build
unzip -l dist/mistral_cli-0.3.0-py3-none-any.whl
unzip -p dist/mistral_cli-0.3.0-py3-none-any.whl mistral_cli-0.3.0.dist-info/METADATA | head -25
```

Expected:
- wheel listing contains `mistral_cli/data/agent_guide.md` and **no** `docs.md` entries;
- METADATA contains `License-Expression: MIT`, `Classifier:` lines, and `Project-URL:` lines.

If hatchling rejects `license = "MIT"` as a string (older backend), pin the build requirement to `hatchling>=1.27` in `[build-system] requires` and rebuild.

- [ ] **Step 5: Run gates and commit**

```bash
rm -rf dist
uv run pytest -q && uv run ruff check . && uv run pyright && uv lock --check
git add LICENSE pyproject.toml uv.lock
git commit -m "chore: add MIT license, packaging metadata, and wheel hygiene; bump to 0.3.0"
```

---

### Task 2: `-h` help alias

**Files:**
- Modify: `src/mistral_cli/cli/main.py`
- Test: `tests/test_cli_root.py`

**Interfaces:**
- Produces: `mistral -h` and `mistral <command> -h` work everywhere (Task 10's README documents this).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_root.py`:

```python
def test_h_is_a_help_alias_for_root_and_subcommands() -> None:
    root = CliRunner().invoke(cli, ["-h"])
    subcommand = CliRunner().invoke(cli, ["ocr", "-h"])

    assert root.exit_code == 0
    assert "Usage:" in root.output
    assert "ocr" in root.output
    assert subcommand.exit_code == 0
    assert "--table-format" in subcommand.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_root.py::test_h_is_a_help_alias_for_root_and_subcommands -v`
Expected: FAIL — exit code 2, "No such option: -h".

- [ ] **Step 3: Implement**

In `src/mistral_cli/cli/main.py`, add `from __future__ import annotations` as the first line of the file, and pass `context_settings` to the group decorator:

```python
@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "Run 'mistral agent' for agent-oriented usage docs and "
        "'mistral agent --schema' for the JSON output schema."
    ),
)
```

(Click context settings are inherited by subcommand contexts, so `ocr`, `transcribe`, `config`, and `agent` all gain `-h`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_root.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
git add src/mistral_cli/cli/main.py tests/test_cli_root.py
git commit -m "feat: accept -h as a help alias on every command"
```

---

### Task 3: Hardening — version fallback, SystemExit, canonical SDK import, dependency upgrade

**Files:**
- Modify: `src/mistral_cli/__init__.py`
- Modify: `src/mistral_cli/cli/runner.py` (3 raise sites)
- Modify: `src/mistral_cli/mistral_client.py:11-18`
- Modify: `uv.lock` (mistralai 2.5.1 → 2.6.0)
- Create: `tests/test_version.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `mistral_cli.__version__` never raises at import; exit codes 1/3 now raised via `SystemExit` (behavior identical under click and CliRunner).

- [ ] **Step 1: Write the failing version-fallback test**

Create `tests/test_version.py`:

```python
from __future__ import annotations

import importlib
import importlib.metadata

import pytest

import mistral_cli


def test_version_is_a_nonempty_version_string() -> None:
    assert mistral_cli.__version__
    assert mistral_cli.__version__[0].isdigit()


def test_version_falls_back_when_distribution_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        module = importlib.reload(mistral_cli)
        assert module.__version__ == "0.0.0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(mistral_cli)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py -v`
Expected: `test_version_falls_back_when_distribution_is_missing` FAILS with `PackageNotFoundError` escaping the reload.

- [ ] **Step 3: Implement the fallback**

Replace the whole of `src/mistral_cli/__init__.py`:

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mistral-cli")
except PackageNotFoundError:  # e.g. vendored source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

Run: `uv run pytest tests/test_version.py -v` — Expected: PASS.

```bash
git add src/mistral_cli/__init__.py tests/test_version.py
git commit -m "fix: fall back to a placeholder version when distribution metadata is missing"
```

- [ ] **Step 4: Replace click.exceptions.Exit with SystemExit**

In `src/mistral_cli/cli/runner.py` change the three raise sites (two in `_create_runtime`, one at the end of `run`):

```python
            raise SystemExit(EXIT_SETUP) from error
```
```python
            raise SystemExit(EXIT_SETUP) from error
```
```python
        if self._failures:
            raise SystemExit(EXIT_FAILURE)
```

`click.exceptions` is no longer referenced in the file — but `click` still is (`click.UsageError` in `_validate_output_options`), so keep the `import click`.

Run: `uv run pytest tests/test_ocr_cli.py tests/test_transcribe_cli.py -q`
Expected: PASS — the existing exit-code assertions (1 on source failure, 3 on setup failure) prove equivalence.

```bash
git add src/mistral_cli/cli/runner.py
git commit -m "refactor: raise SystemExit for batch exit codes instead of click internals"
```

- [ ] **Step 5: Canonical SDK import + upgrade mistralai**

In `src/mistral_cli/mistral_client.py`, replace lines 11–18 (the `TYPE_CHECKING`/try/except block) with a single import placed with the other `from` imports:

```python
from mistralai.client import Mistral
```

The SDK 2.x package root is a namespace package; `mistralai.client` is the canonical module (per the SDK's own README) and ships `py.typed`. Remove `TYPE_CHECKING` from the `typing` import if now unused.

```bash
uv lock --upgrade-package mistralai && uv sync
uv run python -c "import importlib.metadata as im; print(im.version('mistralai'))"
```

Expected: `2.6.0`.

- [ ] **Step 6: Full gates and commit**

```bash
uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run pyright && uv lock --check
git add src/mistral_cli/mistral_client.py uv.lock
git commit -m "refactor: import Mistral from its canonical module; upgrade mistralai to 2.6.0"
```

---

### Task 4: Retries — domain layer (models + dedupe fingerprint)

**Files:**
- Modify: `src/mistral_cli/models.py`
- Modify: `src/mistral_cli/dedupe.py` (`request_fingerprint`)
- Test: `tests/test_models.py`, `tests/test_dedupe.py`

**Interfaces:**
- Produces (Task 5 depends on these exact names):
  - `OcrRequest.retries: int` and `TranscriptionRequest.retries: int` (default `DEFAULT_RETRIES = 3`)
  - `build_ocr_request(..., retries: int = DEFAULT_RETRIES)` / `build_transcription_request(..., retries: int = DEFAULT_RETRIES)` raising `InputError` on invalid values
  - `ocr_request_metadata()` / `transcription_request_metadata()` include a `"retries"` key
  - `request_fingerprint()` ignores `"retries"` (and still ignores `"timeout_ms"`)

- [ ] **Step 1: Write the failing model tests**

Append to `tests/test_models.py` (it already imports `build_ocr_request`, `build_transcription_request`, `ocr_request_metadata`, `transcription_request_metadata`, `InputError`, and has source-building helpers — reuse the file's existing source helper; if none fits, use this local one):

```python
def _retries_source() -> InputSource:
    return InputSource(
        kind=SourceKind.FILE,
        value="doc.pdf",
        filename="doc.pdf",
        path=Path("doc.pdf"),
    )


def test_requests_default_to_three_retries() -> None:
    ocr = build_ocr_request(source=_retries_source(), model="m")
    transcription = build_transcription_request(source=_retries_source(), model="m")

    assert ocr.retries == 3
    assert transcription.retries == 3


@pytest.mark.parametrize("retries", [-1, True, 2.0])
def test_invalid_retries_are_rejected(retries: object) -> None:
    with pytest.raises(InputError, match="--retries"):
        build_ocr_request(
            source=_retries_source(),
            model="m",
            retries=retries,  # type: ignore[arg-type]
        )
    with pytest.raises(InputError, match="--retries"):
        build_transcription_request(
            source=_retries_source(),
            model="m",
            retries=retries,  # type: ignore[arg-type]
        )


def test_request_metadata_includes_retries() -> None:
    ocr = build_ocr_request(source=_retries_source(), model="m", retries=0)
    transcription = build_transcription_request(
        source=_retries_source(), model="m", retries=5
    )

    assert ocr_request_metadata(ocr)["retries"] == 0
    assert transcription_request_metadata(transcription)["retries"] == 5
```

- [ ] **Step 2: Write the failing fingerprint tests**

Append to `tests/test_dedupe.py`:

```python
def test_fingerprint_ignores_retries_and_timeout() -> None:
    noisy = {"model": "m", "timeout_ms": 1, "retries": 9}
    quiet = {"model": "m", "timeout_ms": 300_000, "retries": 0}

    assert request_fingerprint(noisy) == request_fingerprint(quiet)


def test_fingerprint_matches_entries_written_before_retries_existed() -> None:
    legacy = {"model": "m", "pages": None}  # metadata shape from mistral-cli <= 0.2.0
    current = {"model": "m", "pages": None, "retries": 3, "timeout_ms": 300_000}

    assert request_fingerprint(legacy) == request_fingerprint(current)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_dedupe.py -q`
Expected: FAIL — `build_ocr_request() got an unexpected keyword argument 'retries'` and fingerprint mismatch.

- [ ] **Step 4: Implement in models.py**

In `src/mistral_cli/models.py`:

1. Add a module constant near the other constants:

```python
DEFAULT_RETRIES = 3
```

2. Add the field to both request dataclasses, directly above `timeout_ms`:

```python
    retries: int = DEFAULT_RETRIES
    timeout_ms: int = 300_000
```

3. Add a validator next to `_validate_nonnegative_option`:

```python
def _validate_retries(retries: int) -> None:
    if type(retries) is not int or retries < 0:
        raise InputError("--retries must be a nonnegative integer.")
```

4. In `build_ocr_request` and `build_transcription_request`: add the keyword parameter `retries: int = DEFAULT_RETRIES`, call `_validate_retries(retries)` alongside the other validations, and pass `retries=retries` into the constructed request.

5. Add `"retries": request.retries,` to both `ocr_request_metadata` and `transcription_request_metadata` (place it before `"timeout_ms"`).

- [ ] **Step 5: Implement in dedupe.py**

In `request_fingerprint`, extend the exclusion (both keys never affect result content):

```python
    payload = dict(metadata)
    payload.pop("timeout_ms", None)
    payload.pop("retries", None)
```

- [ ] **Step 6: Run tests and the full suite**

Run: `uv run pytest -q`
Expected: PASS (existing dry-run/envelope CLI tests may assert exact request-metadata dicts — if any fail, add `"retries": 3` to their expected dicts; that is the intended contract change).

- [ ] **Step 7: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
git add src/mistral_cli/models.py src/mistral_cli/dedupe.py tests/test_models.py tests/test_dedupe.py tests/test_ocr_cli.py tests/test_transcribe_cli.py
git commit -m "feat: add retries to request models and metadata, excluded from dedupe fingerprints"
```

---

### Task 5: Retries — gateway, CLI options, agent guide

**Files:**
- Modify: `src/mistral_cli/mistral_client.py`
- Modify: `src/mistral_cli/cli/common.py` (shared validator)
- Modify: `src/mistral_cli/cli/ocr.py`, `src/mistral_cli/cli/transcribe.py`
- Modify: `src/mistral_cli/data/agent_guide.md`
- Test: `tests/test_mistral_client.py`, `tests/test_ocr_cli.py`

**Interfaces:**
- Consumes: `OcrRequest.retries` / `TranscriptionRequest.retries`, `build_*_request(retries=...)`, `DEFAULT_RETRIES` (Task 4).
- Produces: `--retries N` CLI option on both commands; gateway passes `RetryConfig` to the SDK when `retries > 0`; `nonnegative_integer` click callback exported from `cli/common.py`.

**Verified SDK facts (do not re-derive):** `mistralai.client.utils.retries` provides `RetryConfig(strategy, backoff, retry_connection_errors)` and `BackoffStrategy(initial_interval, max_interval, exponent, max_elapsed_time)` (all intervals in **milliseconds**). Passing `retries=RetryConfig(...)` to `ocr.process`/`audio.transcriptions.complete` makes the SDK retry HTTP **429/500/502/503/504** and (with `retry_connection_errors=True`) httpx network/timeout errors, honoring `Retry-After`, until `max_elapsed_time` is exhausted. There is no attempt-count knob — the CLI converts N attempts into an equivalent time budget.

- [ ] **Step 1: Write the failing gateway tests**

Append to `tests/test_mistral_client.py` (reuse the file's existing `FakeResponse`/`FakeOcrEndpoint`/`FakeClient` fakes and the factory wiring used by neighboring tests; the assertions below are the essential part):

```python
from mistralai.client.utils.retries import RetryConfig


def test_ocr_passes_backoff_retry_config_for_positive_retries() -> None:
    response = FakeResponse({"pages": []})
    endpoint = FakeOcrEndpoint(response)
    gateway = _gateway_with(ocr_endpoint=endpoint)

    gateway.ocr(_ocr_request(retries=3))

    (call,) = endpoint.calls
    config = call["retries"]
    assert isinstance(config, RetryConfig)
    assert config.strategy == "backoff"
    assert config.retry_connection_errors is True
    # 1s + 2s + 4s of backoff plus 1s jitter allowance per retry = 10s budget
    assert config.backoff.max_elapsed_time == 10_000


def test_ocr_omits_retry_config_when_retries_is_zero() -> None:
    response = FakeResponse({"pages": []})
    endpoint = FakeOcrEndpoint(response)
    gateway = _gateway_with(ocr_endpoint=endpoint)

    gateway.ocr(_ocr_request(retries=0))

    (call,) = endpoint.calls
    assert "retries" not in call
```

Add equivalent `test_transcribe_passes_backoff_retry_config_for_positive_retries` / `..._omits_...` against `FakeTranscriptionEndpoint` using a URL-source `TranscriptionRequest`. Where the file has no `_gateway_with`/`_ocr_request` helpers, construct exactly what its other tests construct (a `MistralGateway(api_key=..., client_factory=...)` around `FakeClient`) — copy the adjacent pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mistral_client.py -q`
Expected: new tests FAIL with `KeyError: 'retries'`.

- [ ] **Step 3: Implement the gateway mapping**

In `src/mistral_cli/mistral_client.py` add near the top (SDK imports stay in this module only):

```python
from mistralai.client.utils.retries import BackoffStrategy, RetryConfig

_RETRY_INITIAL_INTERVAL_MS = 1_000
_RETRY_MAX_INTERVAL_MS = 10_000
_RETRY_EXPONENT = 2.0
_RETRY_JITTER_ALLOWANCE_MS = 1_000


def _retry_config(retries: int) -> RetryConfig | None:
    """Map a retry attempt count onto the SDK's time-budgeted backoff loop."""
    if retries <= 0:
        return None
    budget_ms = 0
    for attempt in range(retries):
        interval = _RETRY_INITIAL_INTERVAL_MS * _RETRY_EXPONENT**attempt
        budget_ms += min(int(interval), _RETRY_MAX_INTERVAL_MS)
        budget_ms += _RETRY_JITTER_ALLOWANCE_MS
    return RetryConfig(
        strategy="backoff",
        backoff=BackoffStrategy(
            initial_interval=_RETRY_INITIAL_INTERVAL_MS,
            max_interval=_RETRY_MAX_INTERVAL_MS,
            exponent=_RETRY_EXPONENT,
            max_elapsed_time=budget_ms,
        ),
        retry_connection_errors=True,
    )
```

In both `MistralGateway.ocr` and `MistralGateway.transcribe`, after building `kwargs`:

```python
        retry_config = _retry_config(request.retries)
        if retry_config is not None:
            kwargs["retries"] = retry_config
```

Run: `uv run pytest tests/test_mistral_client.py -q` — Expected: PASS.

- [ ] **Step 4: Write the failing CLI test**

Append to `tests/test_ocr_cli.py` (uses the file's existing harness fixture that patches `create_gateway`; follow the pattern of the existing dry-run test):

```python
def test_retries_defaults_to_three_and_is_tunable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)  # the file's existing setup helper
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf")

    default_run = harness.runner.invoke(
        cli, ["ocr", "--dry-run", "--json", "--quiet", str(source)]
    )
    tuned_run = harness.runner.invoke(
        cli, ["ocr", "--dry-run", "--json", "--quiet", "--retries", "0", str(source)]
    )
    rejected = harness.runner.invoke(cli, ["ocr", "--retries", "-1", str(source)])

    default_record = json.loads(default_run.output.splitlines()[0])
    tuned_record = json.loads(tuned_run.output.splitlines()[0])
    assert default_record["request"]["retries"] == 3
    assert tuned_record["request"]["retries"] == 0
    assert rejected.exit_code == 2
```

(Adapt the helper name to whatever the file actually uses for CliRunner + fake-gateway setup; the three invocations and assertions are the required content.)

- [ ] **Step 5: Implement the CLI option**

1. Move the `_nonnegative_integer` callback from `src/mistral_cli/cli/ocr.py` to `src/mistral_cli/cli/common.py`, renamed public:

```python
def nonnegative_integer(
    context: click.Context,
    parameter: click.Parameter,
    value: int | None,
) -> int | None:
    """Validate an optional nonnegative integer click option."""
    if value is not None and value < 0:
        raise click.BadParameter(
            "must be a nonnegative integer",
            ctx=context,
            param=parameter,
        )
    return value
```

2. In `ocr.py`: delete the private copy, import `nonnegative_integer` from `mistral_cli.cli.common`, switch the `--image-limit`/`--image-min-size` callbacks to it, and add (next to `--timeout`):

```python
@click.option(
    "--retries",
    type=int,
    default=3,
    show_default=True,
    callback=nonnegative_integer,
    help="Retry attempts for rate-limited, server-error, and connection failures (0 disables).",
)
```

Add `retries: int` to the command function signature and pass `retries=retries` into `build_ocr_request`.

3. Same option, signature parameter, and `retries=retries` pass-through in `transcribe.py` (`build_transcription_request`).

- [ ] **Step 6: Update the agent guide**

In `src/mistral_cli/data/agent_guide.md`:
- Shared options line becomes:

```markdown
Shared options: `--json`, `--stdout`, `--quiet`, `--no-save`, `--dry-run`,
`--output-dir DIR`, `--format md|json|both` (saved files), `--timeout SECONDS`,
`--retries N` (default 3; 0 disables), `--force`, `--dedupe-window DAYS`.
```

- Append to the "Cost and scope control" section:

```markdown
Transient failures (HTTP 429/5xx, connection errors) are retried with
exponential backoff (`--retries`, default 3). Retries add latency, never
cost — a request that never succeeded is not billed.
```

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run pyright
git add src/mistral_cli/mistral_client.py src/mistral_cli/cli/common.py src/mistral_cli/cli/ocr.py src/mistral_cli/cli/transcribe.py src/mistral_cli/data/agent_guide.md tests/test_mistral_client.py tests/test_ocr_cli.py
git commit -m "feat: retry transient API failures with exponential backoff (--retries, default 3)"
```

---

### Task 6: OCR inline size guard

**Files:**
- Modify: `src/mistral_cli/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `_is_audio_purpose`, `InputError` (existing).
- Produces: local OCR sources larger than 50 MB fail fast with `InputError` before any API-key/network work; audio sources are unaffected.

Framing (from the spec): the limit is a property of this CLI's inline base64 upload mechanism — current Mistral docs no longer publish a per-document OCR byte ceiling, so the message talks about the CLI's mechanism, not a claimed API limit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sources.py` (sparse files keep this fast):

```python
def _file_of_size(tmp_path: Path, name: str, size: int) -> Path:
    path = tmp_path / name
    with path.open("wb") as handle:
        if size:
            handle.seek(size - 1)
            handle.write(b"\x00")
    return path


def test_local_ocr_source_over_50mb_is_rejected(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "big.pdf", limit + 1)

    with pytest.raises(InputError, match="50 MB"):
        resolve_source(str(path), Operation.OCR)


def test_local_ocr_source_at_exactly_50mb_is_accepted(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "edge.pdf", limit)

    source = resolve_source(str(path), Operation.OCR)

    assert source.filename == "edge.pdf"


def test_oversized_audio_source_is_not_size_limited(tmp_path: Path) -> None:
    limit = 50 * 1024 * 1024
    path = _file_of_size(tmp_path, "long.mp3", limit + 1)

    source = resolve_source(str(path), Operation.TRANSCRIPTION)

    assert source.filename == "long.mp3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -q`
Expected: `test_local_ocr_source_over_50mb_is_rejected` FAILS (no exception raised).

- [ ] **Step 3: Implement**

In `src/mistral_cli/sources.py`, add a constant near the other module constants:

```python
_MAX_INLINE_DOCUMENT_BYTES = 50 * 1024 * 1024
```

In `_resolve_local`, capture the purpose check result (first line becomes `audio = _is_audio_purpose(purpose)`) and add the guard inside the existing `try` block, after the `is_file()` check:

```python
        if not audio:
            size = path.stat().st_size
            if size > _MAX_INLINE_DOCUMENT_BYTES:
                raise InputError(
                    f"source file is {size} bytes; local OCR sources are sent "
                    "inline as base64 and are limited to 50 MB. Reduce the "
                    "document or host it at an HTTP(S) URL instead."
                )
```

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/test_sources.py -q && uv run pytest -q
uv run ruff format . && uv run ruff check . && uv run pyright
git add src/mistral_cli/sources.py tests/test_sources.py
git commit -m "feat: reject local OCR sources over 50 MB before any network work"
```

---

### Task 7: Drop the rich dependency

**Files:**
- Modify: `src/mistral_cli/console.py` (rewrite)
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/test_console.py` (rewrite)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ConsoleBundle` with **unchanged** `write_stdout(payload: str)` / `write_stderr(payload: str)` methods (runner.py, common.py, agent.py never change); `create_console_bundle()` now returns `sys.stdout`/`sys.stderr`-backed streams. The rich helpers (`print`, `print_status`, `print_error`, `status`, `print_debug_exception`) are deleted — they are dead code with no production callers (verified in review).

- [ ] **Step 1: Rewrite console.py**

Keep everything from the top of the file through `sanitize_terminal_text` **unchanged** (the escape-sequence parser is the module's core). Replace the imports and everything after `sanitize_terminal_text` so the module reads:

```python
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

# ... existing _ESC/_BEL/... constants and _consume_*/sanitize_terminal_text
# functions stay exactly as they are ...


class TextStream(Protocol):
    """The minimal writable text stream ConsoleBundle needs."""

    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ConsoleBundle:
    """Application output streams behind sanitizing write helpers."""

    stdout: TextStream
    stderr: TextStream

    def write_stdout(self, payload: str) -> None:
        self.stdout.write(sanitize_terminal_text(payload))
        self.stdout.flush()

    def write_stderr(self, payload: str) -> None:
        self.stderr.write(sanitize_terminal_text(payload))
        self.stderr.flush()


def create_console_bundle() -> ConsoleBundle:
    """Create the bundle over the process standard streams."""
    return ConsoleBundle(stdout=sys.stdout, stderr=sys.stderr)
```

Delete the `rich` imports, `_safe_text`, and the five dead methods; the `format_debug_exception` import goes too.

- [ ] **Step 2: Rewrite test_console.py**

Replace `tests/test_console.py` with:

```python
from __future__ import annotations

import sys
from io import StringIO

from mistral_cli.console import (
    ConsoleBundle,
    create_console_bundle,
    sanitize_terminal_text,
)


class FlushTrackingStringIO(StringIO):
    flushed = False

    def flush(self) -> None:
        self.flushed = True
        super().flush()


def _assert_no_unsafe_controls(text: str) -> None:
    for character in text:
        codepoint = ord(character)
        assert character in "\n\t" or (
            codepoint >= 0x20 and codepoint != 0x7F and not 0x80 <= codepoint <= 0x9F
        )


def test_sanitize_terminal_text_removes_terminal_controls() -> None:
    untrusted = (
        "safe\tline\n"
        "\x1b[31mred\x1b[0m"
        "\x1b]0;owned-title\x07visible"
        "\x1b]8;;https://evil.example\x1b\\link\x1b]8;;\x1b\\"
        "\x1bPprivate-dcs\x1b\\"
        "\x1bcreset"
        "\rreturn\bback\x00\x7f"
        "\x9b2Jafter-csi"
        "\x90private-c1-dcs\x9c"
        "\x85unicode-雪"
    )

    sanitized = sanitize_terminal_text(untrusted)

    assert sanitized == ("safe\tline\nredvisiblelinkresetreturnbackafter-csiunicode-雪")
    _assert_no_unsafe_controls(sanitized)


def test_write_stdout_preserves_raw_payload_and_flushes() -> None:
    stdout = FlushTrackingStringIO()
    stderr = StringIO()
    consoles = ConsoleBundle(stdout=stdout, stderr=stderr)
    payload = (
        "# Markdown\n"
        + "a" * 80
        + '\n{"value":"unchanged"}'
        + "\x1b]0;hidden-title\x07"
    )

    consoles.write_stdout(payload)

    assert stdout.getvalue() == "# Markdown\n" + "a" * 80 + '\n{"value":"unchanged"}'
    assert not stdout.getvalue().endswith("\n")
    assert stdout.flushed is True
    assert stderr.getvalue() == ""


def test_write_stderr_routes_to_stderr_only_and_sanitizes() -> None:
    stdout = StringIO()
    stderr = FlushTrackingStringIO()
    consoles = ConsoleBundle(stdout=stdout, stderr=stderr)
    untrusted = "report\x1b[2J.pdf\x1b]0;hijacked\x07\rrewritten\n"

    consoles.write_stderr(untrusted)

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "report.pdfrewritten\n"
    assert stderr.flushed is True
    _assert_no_unsafe_controls(stderr.getvalue())


def test_production_factory_uses_the_process_standard_streams() -> None:
    consoles = create_console_bundle()

    assert consoles.stdout is sys.stdout
    assert consoles.stderr is sys.stderr
```

- [ ] **Step 3: Remove the dependency and relock**

Remove the `"rich>=15.0.0,<16",` line from `[project] dependencies` in `pyproject.toml`, then:

```bash
uv lock && uv sync
grep -rn "rich" src/ && echo "FAIL: rich still referenced" || echo "OK: no rich in src"
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. If any other test file constructs `ConsoleBundle` with `rich.console.Console` objects, switch those constructions to `StringIO` — the `write_*` call sites need no changes.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv lock --check
git add src/mistral_cli/console.py tests/test_console.py pyproject.toml uv.lock
git commit -m "refactor: drop rich; ConsoleBundle writes to plain text streams"
```

---

### Task 8: storage.py simplification

**Files:**
- Modify: `src/mistral_cli/storage.py` (full rewrite, ~569 → ~170 lines)
- Test: `tests/test_storage.py` (delete internals tests, adapt three, add one, guard POSIX asserts)

**Interfaces:**
- Consumes: nothing new.
- Produces (unchanged, other modules depend on them): `utc_now()`, `get_cli_version()`, `ResultStore(base_dir=None, clock=utc_now, version=get_cli_version)`, `.base_dir` property, `.save(result, markdown, output_format, output_dir=None) -> SavedResult`, `PersistenceError` on all failures, filename scheme `{UTC timestamp}[-{suffix}]-{fitted filename}{.md|.json}`.
- Guarantees preserved: never overwrites an existing file (`os.link` fails `EEXIST` atomically); visible files are always complete (content fully written + fsynced before linking); 0600 modes on POSIX.

- [ ] **Step 1: Replace storage.py**

Full new content of `src/mistral_cli/storage.py`:

```python
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from mistral_cli import __version__
from mistral_cli.errors import PersistenceError
from mistral_cli.formatters import build_envelope, serialize_json
from mistral_cli.models import ApiResult, Operation, OutputFormat, SavedResult

_FALLBACK_NAME_MAX = 255
_MAX_PRESERVED_EXTENSION_BYTES = 16


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_cli_version() -> str:
    return __version__


def _set_private_mode(fd: int, path: Path) -> None:
    if os.name != "posix":
        return
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(fd, 0o600)
    else:
        path.chmod(0o600)


def _write_temp_file(content: bytes, directory: Path) -> Path:
    """Write content to a private, fully synced temp file inside directory."""
    fd, temp_name = tempfile.mkstemp(
        prefix=".mistral-cli-",
        suffix=".tmp",
        dir=directory,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output:
            _set_private_mode(output.fileno(), temp_path)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _unlink_quietly(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _link_into_place(
    temps: dict[str, Path],
    destinations: dict[str, Path],
) -> bool:
    """Atomically link every artifact into place without overwriting.

    Returns False when any destination name is already taken so the caller
    can retry with the next collision suffix.
    """
    created: list[Path] = []
    for extension, destination in destinations.items():
        try:
            os.link(temps[extension], destination)
        except FileExistsError:
            _unlink_quietly(created)
            return False
        except OSError:
            _unlink_quietly(created)
            raise
        created.append(destination)
    return True


def _name_max(directory: Path) -> int:
    pathconf = getattr(os, "pathconf", None)
    if callable(pathconf):
        try:
            value = pathconf(directory, "PC_NAME_MAX")
        except (OSError, TypeError, ValueError):
            pass
        else:
            if isinstance(value, int) and value > 0:
                return min(value, _FALLBACK_NAME_MAX)
    return _FALLBACK_NAME_MAX


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _fit_source_filename(filename: str, byte_limit: int) -> str:
    if len(filename.encode("utf-8")) <= byte_limit:
        return filename

    extension = Path(filename).suffix
    extension_size = len(extension.encode("utf-8"))
    if (
        extension
        and extension_size <= _MAX_PRESERVED_EXTENSION_BYTES
        and extension_size < byte_limit
    ):
        stem = filename[: -len(extension)]
        return _truncate_utf8(stem, byte_limit - extension_size) + extension
    return _truncate_utf8(filename, byte_limit)


def _candidate_destinations(
    *,
    directory: Path,
    timestamp: str,
    suffix: int,
    source_filename: str,
    extensions: tuple[str, ...],
    name_max: int,
) -> dict[str, Path]:
    counter = "" if suffix == 0 else f"-{suffix}"
    prefix = f"{timestamp}{counter}-"
    byte_limit = (
        name_max
        - len(prefix.encode("utf-8"))
        - max(len(extension.encode("utf-8")) for extension in extensions)
    )
    if byte_limit <= 0:
        raise OSError("filesystem name limit is too small for result names")
    fitted_filename = _fit_source_filename(source_filename, byte_limit)
    base_name = f"{prefix}{fitted_filename}"
    return {
        extension: directory / f"{base_name}{extension}" for extension in extensions
    }


class ResultStore:
    def __init__(
        self,
        base_dir: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
        version: Callable[[], str] = get_cli_version,
    ) -> None:
        self._base_dir = (
            Path("~/.mistral").expanduser() if base_dir is None else base_dir
        )
        self._clock = clock
        self._version = version

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save(
        self,
        result: ApiResult,
        markdown: str,
        output_format: OutputFormat,
        output_dir: Path | None = None,
    ) -> SavedResult:
        saved_at = self._clock()
        if saved_at.utcoffset() is None:
            raise PersistenceError("Save clock must return a timezone-aware datetime.")
        timestamp = saved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")

        if output_format is OutputFormat.MD:
            extensions = (".md",)
        elif output_format is OutputFormat.JSON:
            extensions = (".json",)
        elif output_format is OutputFormat.BOTH:
            extensions = (".md", ".json")
        else:
            raise PersistenceError(f"Unsupported output format: {output_format!s}.")

        contents: dict[str, bytes] = {}
        if ".md" in extensions:
            try:
                contents[".md"] = markdown.encode("utf-8")
            except UnicodeError as error:
                raise PersistenceError(
                    f"Could not encode result Markdown as UTF-8: {error}."
                ) from error
        if ".json" in extensions:
            try:
                contents[".json"] = serialize_json(
                    build_envelope(result, self._version())
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError) as error:
                raise PersistenceError(
                    f"Could not serialize result JSON: {error}."
                ) from error

        destination_dir = output_dir or (
            self._base_dir
            / ("ocr" if result.operation is Operation.OCR else "transcriptions")
        )
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PersistenceError(
                f"Could not create result directory '{destination_dir}': {error}."
            ) from error

        name_max = _name_max(destination_dir)
        temps: dict[str, Path] = {}
        try:
            try:
                for extension in extensions:
                    temps[extension] = _write_temp_file(
                        contents[extension], destination_dir
                    )
            except OSError as error:
                raise PersistenceError(
                    f"Could not save result in '{destination_dir}': {error}."
                ) from error

            suffix = 0
            while True:
                try:
                    destinations = _candidate_destinations(
                        directory=destination_dir,
                        timestamp=timestamp,
                        suffix=suffix,
                        source_filename=result.source.filename,
                        extensions=extensions,
                        name_max=name_max,
                    )
                except OSError as error:
                    raise PersistenceError(
                        f"Could not prepare result path in "
                        f"'{destination_dir}': {error}."
                    ) from error
                try:
                    published = _link_into_place(temps, destinations)
                except OSError as error:
                    raise PersistenceError(
                        f"Could not save result in '{destination_dir}': {error}."
                    ) from error
                if published:
                    return SavedResult(
                        markdown=destinations.get(".md"),
                        json=destinations.get(".json"),
                    )
                suffix += 1
        finally:
            _unlink_quietly(temps.values())
```

- [ ] **Step 2: Prune the internals tests**

In `tests/test_storage.py`, delete these tests (they exercise machinery that no longer exists — platform rename dispatch, reservation locks, quarantine/restore):

- `test_atomic_noreplace_move_never_overwrites_existing_entry`
- `test_atomic_noreplace_move_dispatches_to_safe_platform_helper`
- `test_atomic_noreplace_move_rejects_unsupported_platform`
- `test_publish_race_rolls_back_only_this_attempt_and_retries`
- `test_rollback_never_deletes_replacement_after_identity_check`
- `test_rollback_restores_foreign_symlink_without_following_it`
- `test_rollback_restores_foreign_directory_with_its_contents`
- `test_rollback_reports_quarantine_if_destination_becomes_occupied`
- `test_rollback_never_overwrites_occupied_quarantine`
- `test_collision_check_failure_is_translated` (the pre-publish `exists()` scan is gone; link-EEXIST covers it)

Also delete any now-unused imports/helpers those tests carried (parametrize fixtures for platform dispatch, fake `os.link`/`os.stat` identity wrappers).

- [ ] **Step 3: Adapt the failure-path tests and add the race test**

Replace `test_link_failure_leaves_no_partial_or_temp_files` and `test_write_failure_is_translated_and_temp_is_removed` with the following (reuse the file's existing result-builder helper; if the deleted tests owned it, keep the helper). If no suitable helper survives, use this self-contained one:

```python
def _sample_result() -> ApiResult:
    return ApiResult(
        operation=Operation.OCR,
        source=InputSource(
            kind=SourceKind.FILE,
            value="doc.pdf",
            filename="doc.pdf",
            path=Path("doc.pdf"),
        ),
        request_metadata={"model": "m"},
        response={"pages": []},
        created_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_link_failure_is_translated_and_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_link(source: str, destination: str) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(storage.os, "link", failing_link)
    store = ResultStore(base_dir=tmp_path)

    with pytest.raises(PersistenceError, match="Could not save result"):
        store.save(_sample_result(), "# md\n", OutputFormat.BOTH)

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_write_failure_is_translated_and_temp_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fsync(fd: int) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(storage.os, "fsync", failing_fsync)
    store = ResultStore(base_dir=tmp_path)

    with pytest.raises(PersistenceError, match="Could not save result"):
        store.save(_sample_result(), "# md\n", OutputFormat.MD)

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_race_on_first_name_falls_back_to_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link
    calls = {"count": 0}

    def racing_link(source: str, destination: str) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            # Another process claims the destination between candidate
            # generation and our link.
            Path(destination).write_text("winner", encoding="utf-8")
        real_link(source, destination)

    monkeypatch.setattr(storage.os, "link", racing_link)
    store = ResultStore(base_dir=tmp_path)

    saved = store.save(_sample_result(), "# md\n", OutputFormat.MD)

    assert saved.markdown is not None
    assert "-1-" in saved.markdown.name
    assert saved.markdown.read_text(encoding="utf-8") == "# md\n"
```

- [ ] **Step 4: Make POSIX-mode assertions Windows-honest**

In `test_save_honors_format_and_exact_custom_directory` (currently around lines 157–165), wrap the two `st_mode & 0o777 == 0o600` assertions:

```python
    if os.name == "posix":
        assert saved.markdown.stat().st_mode & 0o777 == 0o600
```

(same for the `.json` assertion). Keep `test_save_works_when_fchmod_is_unavailable` and `test_private_mode_skips_fchmod_on_non_posix_platform` — the `_set_private_mode` helper they exercise still exists.

- [ ] **Step 5: Run the storage suite, then everything**

```bash
uv run pytest tests/test_storage.py -v
uv run pytest -q
```

Expected: PASS. Behavior tests (`test_default_directories...`, `test_collision_suffixes_cover_the_entire_requested_set`, `test_existing_file_is_never_overwritten`, filename fitting, naive-clock rejection, serialization ordering) must pass without modification — if one fails, the rewrite broke a guarantee: fix the code, not the test.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright
git add src/mistral_cli/storage.py tests/test_storage.py
git commit -m "refactor: rewrite result storage around atomic no-replace hard links"
```

---

### Task 9: CI workflow and CHANGELOG

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: the badge URL Task 10's README uses: `https://github.com/maxboettinger/mistral-cli/actions/workflows/ci.yml/badge.svg`.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  checks:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ matrix.python }}
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest
      - run: uv build
```

(`uv sync --locked` fails on a stale lockfile, subsuming the `uv lock --check` gate.)

- [ ] **Step 2: Validate the YAML**

```bash
uv run python -c "import tomllib" && python3 -c "
import yaml, pathlib
yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())
print('YAML OK')
" 2>/dev/null || uv run --with pyyaml python -c "
import yaml, pathlib
yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())
print('YAML OK')
"
```

Expected: `YAML OK`. (The first real Windows run happens on push; if it reveals genuine Windows bugs, fixing them is in scope — file follow-up commits.)

- [ ] **Step 3: Create the CHANGELOG**

Confirm release dates first: `git log --oneline --format="%h %ad %s" --date=short | tail -20`. Then create `CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project are documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-12

### Added

- MIT license and complete packaging metadata (authors, keywords,
  classifiers, project URLs).
- `-h` as an alias for `--help` on every command.
- `--retries N` on `ocr` and `transcribe` (default 3): transient API
  failures (HTTP 429/5xx, connection errors) are retried with exponential
  backoff; `0` disables.
- Pre-flight size guard: local OCR sources larger than 50 MB fail fast with
  a clear error instead of an opaque API failure.
- GitHub Actions CI across Python 3.11–3.13 on Linux, macOS, and Windows.
- This changelog.

### Changed

- Removed the `rich` dependency; output is written directly to the standard
  streams (behavior unchanged).
- Rewrote result storage around atomic no-replace hard links — same
  collision-safety and durability guarantees, about a quarter of the code.
- Import the Mistral SDK client from its canonical module and upgrade
  `mistralai` to 2.6.0.
- Internal development docs are no longer shipped in the wheel.

### Fixed

- `mistral_cli.__version__` no longer crashes at import when distribution
  metadata is unavailable.

## [0.2.0] - 2026-07-03

### Added

- Content-addressed duplicate detection: identical recent sources are
  skipped before any API call (`--force`, `--dedupe-window`).
- `mistral agent` command with a packaged usage guide and
  `--schema` JSON Schema for the NDJSON output records.
- Stable NDJSON `--json` output contract and documented exit codes.

## [0.1.0] - 2026-07-02

### Added

- Initial release: `mistral ocr` (documents/images → Markdown/JSON) and
  `mistral transcribe` (audio → text) with batch processing, durable saved
  results, secure API-key handling, and stdout/stderr output discipline.
```

(Adjust the 0.1.0/0.2.0 dates if `git log` disagrees.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml CHANGELOG.md
git commit -m "ci: add cross-platform quality-gate workflow and changelog"
```

---

### Task 10: README/docs sync and final acceptance

**Files:**
- Modify: `README.md`
- Test: full gates + clean-venv install (no new test files)

**Interfaces:**
- Consumes: badge URL (Task 9), `--retries` semantics (Task 5), `-h` (Task 2), LICENSE (Task 1).

- [ ] **Step 1: README edits**

1. Directly under the `# mistral-cli` heading, add badges:

```markdown
[![CI](https://github.com/maxboettinger/mistral-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/maxboettinger/mistral-cli/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
```

2. In Quickstart, replace the install commands with a clone-based flow (keep the forced-rebuild note and OpenStack warning):

```console
git clone https://github.com/maxboettinger/mistral-cli.git
uv tool install ./mistral-cli
```

and replace the recovery example's `uv tool install ~/github/mistral-cli` with `uv tool install ./mistral-cli`.

3. In both command option tables, add a row (after `--timeout`):

```markdown
| `--retries N` | Retry attempts for rate-limit/server/connection failures; `0` disables. Default: `3`. |
```

4. In "Errors, debugging & security", add before the security list:

```markdown
Transient failures — HTTP 429 rate limits, 5xx server errors, dropped
connections — are retried automatically with exponential backoff
(`--retries`, default 3). A request that never succeeded is never billed.
```

5. Add a "Shell completion" section at the end of Configuration:

```markdown
### Shell completion

Click provides tab completion for commands and options. Add the line for
your shell to its startup file:

```console
eval "$(_MISTRAL_COMPLETE=zsh_source mistral)"     # ~/.zshrc
eval "$(_MISTRAL_COMPLETE=bash_source mistral)"    # ~/.bashrc
_MISTRAL_COMPLETE=fish_source mistral | source     # ~/.config/fish/config.fish
```
```

6. Mention 50 MB in the OCR section note: extend the "Public URLs must be directly reachable…" callout with: `Local files are limited to 50 MB (they are sent inline as base64); larger documents should be hosted at a URL.`

7. In the architecture table, change the `cli/` row description from "Click commands, validation flow, and Rich console reporting." to "Click commands, validation flow, and sanitized console reporting." Remove the Rich documentation link from References.

8. In Requirements, note help discoverability: add `- Every command supports \`-h\`/\`--help\`` is unnecessary — skip; instead ensure the Usage section's first example block shows `mistral ocr --help`. (Optional; skip if it reads worse.)

9. Link the changelog: add `See [CHANGELOG.md](CHANGELOG.md) for release history.` at the end of the Development section.

- [ ] **Step 2: Full gates**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv lock --check
uv build
```

Expected: all green.

- [ ] **Step 3: Wheel + clean-venv acceptance**

```bash
unzip -l dist/mistral_cli-0.3.0-py3-none-any.whl | grep -c "docs\.md" || true   # expect 0
unzip -l dist/mistral_cli-0.3.0-py3-none-any.whl | grep agent_guide             # expect 1 line
S=$(mktemp -d)
uv venv -q "$S/venv"
VIRTUAL_ENV="$S/venv" uv pip install -q dist/mistral_cli-0.3.0-py3-none-any.whl
"$S/venv/bin/mistral" --version          # expect: mistral, version 0.3.0
"$S/venv/bin/mistral" -h | head -3       # expect: Usage: mistral ...
printf 'x' > "$S/sample.txt"
HOME="$S/fakehome" "$S/venv/bin/mistral" ocr --dry-run --json --quiet "$S/sample.txt"
# expect: one dry_run record with "retries":3, one summary record, exit 0
rm -rf "$S" dist
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: badges, clone-based install, retries, completion, changelog link"
```

- [ ] **Step 5: Update internal docs (nori)**

Invoke the `updating-noridocs` skill to sync `src/mistral_cli/docs.md`, `src/mistral_cli/cli/docs.md`, and `src/mistral_cli/services/docs.md` with the changes (console rewrite, storage rewrite, retries). Also update `CLAUDE.md` if any of its statements became false — expected delta: none required (its invariants were written mechanism-agnostically), but verify the `console.py`/`storage.py` module-table lines still read true.

```bash
git add -A && git commit -m "docs: sync internal architecture docs"
```
