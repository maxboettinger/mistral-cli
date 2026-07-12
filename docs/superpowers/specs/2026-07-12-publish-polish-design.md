# Publish-ready polish — design

Date: 2026-07-12
Status: approved

## Goal

Take mistral-cli from "excellent private tool" to a publishable, featurable
GitHub project: complete legal/packaging metadata, CI, README polish, `-h`
support, SDK retries, removal of the unused rich dependency, a drastically
simpler `storage.py`, and a set of small correctness/hygiene fixes identified
in the 2026-07-12 full code review. Version bumps to **0.3.0**.

Decisions locked with the user:

- License: **MIT**
- Rich: **drop it** (do not build a spinner)
- Retries: **on by default (3), `--retries N` to tune, `0` disables**
- `storage.py`: **simplify in this round**

## Non-goals (deliberately skipped)

- De-duplicating `_utc_now` between `services/*` and `storage.py` — fixing it
  would make `services/` import infrastructure; 3 duplicated lines are the
  lesser evil.
- Dedupe-index compaction — append-only NDJSON growth is irrelevant at
  personal scale (YAGNI).
- Demo GIF/asciinema for the README — cannot be recorded in this environment.
- Files-upload + signed-URL flow for large OCR documents — out of scope; the
  size guard (below) gives a clear error instead.

## 1. Legal & metadata

- New `LICENSE` file: MIT, copyright 2026 Max Boettinger.
- `pyproject.toml` `[project]` gains:
  - `license = "MIT"` (PEP 639 SPDX expression) and
    `license-files = ["LICENSE"]`
  - `authors = [{ name = "Max Boettinger" }]`
  - `keywords` (ocr, transcription, mistral, cli, markdown, speech-to-text…)
  - `classifiers`: Development Status :: 4 - Beta, Environment :: Console,
    Intended Audience :: Developers, Operating System :: OS Independent,
    Programming Language :: Python :: 3.11/3.12/3.13, Topic :: Text
    Processing, Typing :: Typed. **No license classifier** (deprecated by
    PEP 639 when an SPDX expression is present).
  - `[project.urls]`: Repository, Issues, Changelog (GitHub URLs).
- `version = "0.3.0"`.
- Verify hatchling builds the metadata (PEP 639 needs a recent hatchling; if
  the pinned build backend rejects `license = "MIT"` as a string, fall back
  to the classic table form — decided at implementation time by building).

## 2. CI & repo hygiene

- `.github/workflows/ci.yml`:
  - Trigger: push to `main`, pull_request.
  - `astral-sh/setup-uv` with uv-managed Python.
  - Matrix: Python 3.11 / 3.12 / 3.13 × ubuntu-latest / macos-latest /
    windows-latest.
  - Steps: `uv sync --locked`, `uv run ruff check .`,
    `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`,
    `uv build`. (`uv sync --locked` subsumes `uv lock --check`.)
- Windows honesty: audit tests for POSIX-only assertions (file modes 0600/0700,
  `os.fchmod`, symlink behavior) and guard them with
  `pytest.mark.skipif(os.name != "posix", ...)` so the Windows leg is real.
  The first CI run is the actual Windows verification; fixing any genuine
  Windows product bugs it reveals is in scope.
- `CHANGELOG.md` in Keep a Changelog format: 0.1.0 and 0.2.0 reconstructed
  from git history, 0.3.0 for this work.
- Wheel hygiene: `[tool.hatch.build.targets.wheel] exclude = ["**/docs.md"]`
  so the internal nori dev docs (`mistral_cli/docs.md`, `cli/docs.md`,
  `services/docs.md`) stop shipping. `data/agent_guide.md` is unaffected
  (different filename). Verified by listing the built wheel.

## 3. CLI UX

- Root group gets
  `context_settings={"help_option_names": ["-h", "--help"]}` — inherited by
  every subcommand through the context tree. Verified for root and one
  subcommand in tests.
- `cli/main.py` also gains the missing `from __future__ import annotations`.
- README changes:
  - Badges at top: CI status, Python ≥3.11, MIT license.
  - Install flow becomes `git clone` + `uv tool install ./mistral-cli`
    (drop the personal `~/github/mistral-cli` path).
  - New "Shell completion" section using click's built-in mechanism
    (`_MISTRAL_COMPLETE=zsh_source mistral`, plus bash/fish variants).
  - Document `--retries` in both option tables and prose.
  - Remove the Rich reference from the References section and architecture
    table.
  - Link CHANGELOG.

## 4. Retries (new feature)

- New option on `ocr` and `transcribe`:
  `--retries N` — "Retry attempts for rate-limited, server-error, and
  connection failures (0 disables)." Default **3**, validated nonnegative
  integer (reuse the `_nonnegative_integer` callback pattern; move it to
  `cli/common.py` since both commands need it).
- Plumbing mirrors `timeout`:
  - `OcrRequest`/`TranscriptionRequest` gain `retries: int = 3`.
  - `build_ocr_request`/`build_transcription_request` accept `retries` and
    validate (nonnegative int; reject bool).
  - `ocr_request_metadata`/`transcription_request_metadata` include
    `"retries"` so it lands in the envelope, NDJSON, and `--dry-run` output.
  - `request_fingerprint` pops `"retries"` alongside `"timeout_ms"` —
    retries do not affect result content, and existing dedupe indexes must
    keep matching after upgrade (no invisible reprocessing).
- Gateway (`mistral_client.py`): translate `request.retries` into the SDK's
  `RetryConfig` (strategy "backoff", exponential backoff, initial interval
  ~500 ms, max interval ~10 s, `retry_connection_errors=True`,
  `max_elapsed_time` chosen so it cooperates with `timeout_ms`); `retries=0`
  passes no config (SDK fail-fast default).
- **Verification step (mandatory):** inspect the installed SDK's generated
  retry path to confirm a per-call `RetryConfig` actually retries HTTP
  429/5xx (speakeasy SDKs need status codes wired per-operation). If the SDK
  only retries connection errors, say so and adjust (e.g., wrap with our own
  status-code retry loop in the gateway) rather than shipping a placebo flag.
- `agent_guide.md`: add `--retries` to shared options and one line under
  cost control (retries multiply latency, not cost, for idempotent reads).
- Tests (TDD): validator accepts 0/3, rejects negatives and bools; metadata
  contains `retries`; fingerprint unchanged vs. a pre-change golden value;
  gateway passes/omits the config; a fake-gateway test proving CLI plumbing.

## 5. Drop Rich

- `console.py`:
  - `ConsoleBundle` becomes a frozen dataclass holding `stdout: TextIO` and
    `stderr: TextIO`; `write_stdout`/`write_stderr` keep their exact
    sanitize-then-write-then-flush behavior.
  - Delete dead helpers: `print`, `print_status`, `print_error`, `status`,
    `print_debug_exception`, `_safe_text`, and the now-unused
    `format_debug_exception` import.
  - `create_console_bundle()` returns `sys.stdout`/`sys.stderr`.
  - `sanitize_terminal_text` and all its logic are untouched.
- `pyproject.toml`: remove `rich` dependency; relock.
- Tests: delete the dead-helper tests in `test_console.py`; keep and adapt
  sanitize + `write_*` tests to plain `io.StringIO` streams.
- CLAUDE.md / README: remove Rich mentions; `ConsoleBundle` description
  updated.

## 6. storage.py simplification (~569 → ~150 lines)

Public surface preserved exactly: `ResultStore(base_dir, clock, version)`,
`.base_dir`, `.save(result, markdown, output_format, output_dir) -> SavedResult`,
`utc_now`, `get_cli_version`, the `timestamp[-suffix]-filename.ext` naming
scheme, filename fitting (`_fit_source_filename` and NAME_MAX handling stay),
and `PersistenceError` for all failures.

Hard guarantees preserved:

1. **Never overwrite an existing file** — publication is
   `os.link(temp, destination)`, which fails `EEXIST` atomically.
2. **Visible files are always complete** — content is fully written and
   fsynced to a `mkstemp` temp file (mode 0600 on POSIX) before the link.

What is deleted:

- The ctypes `renameat2`/`renamex_np` bindings and `_rename_noreplace`.
- The quarantine/restore rollback machinery (`_remove_created`,
  `_restore_*`, `_preservation_error`, file-identity tracking).
- The SHA-based reservation lock (`_acquire_reservation`) — redundant with
  link-EEXIST collision detection.

New failure/cleanup semantics: on any error mid-save, unlink exactly the
destination paths and temp files this call created, best-effort, then raise
`PersistenceError`. On `EEXIST` from any link, unlink this attempt's files,
bump the suffix, retry. (The theoretical race where another process replaces
our just-linked file before rollback is accepted; the old 300-line quarantine
system existed only for that.)

Tests: behavior-level tests (collision suffixes, content, naming, permissions,
error mapping) must pass unchanged or with mechanical adjustments; tests that
target deleted internals are removed. Concurrency/collision coverage is kept
by asserting suffix behavior when destinations pre-exist.

## 7. Small fixes

- `mistral_client.py`: single canonical import
  `from mistralai.client import Mistral` (root import never worked in SDK
  2.x; comment removed). TYPE_CHECKING branch collapses too.
- `uv lock --upgrade-package mistralai` → 2.6.0 (constraint stays
  `>=2.5.1,<3` unless the retries API requires the floor to move).
- OCR size guard: in `sources._resolve_local`, when purpose is OCR, stat the
  file and raise `InputError` if it exceeds **50 MB** (Mistral's documented
  OCR limit — re-verify the number against current docs during
  implementation; adjust the constant if the docs say otherwise). Message
  names the limit and suggests `--pages`/splitting. Audio gets no guard
  (no reliably documented limit).
- `__init__.py`: wrap `version("mistral-cli")` in
  `try/except PackageNotFoundError` → `"0.0.0+unknown"`.
- Replace the three `click.exceptions.Exit` raises in `runner.py` with
  `SystemExit` (stdlib, same exit-code behavior under click and CliRunner —
  verified by the existing exit-code tests).

## 8. Docs sync (last)

- README (per section 3), `agent_guide.md` (per section 4), CLAUDE.md
  (console/storage/retries descriptions), CHANGELOG 0.3.0, and the nori
  `docs.md` files via the updating-noridocs flow.
- The stable machine contract is unchanged except for the additive
  `"retries"` key inside envelope `request` objects — `schema_version` stays
  1 (the schema declares `request` as an open object; additive keys are
  non-breaking).

## Acceptance

- All six gates green: `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run pyright`, `uv lock --check`,
  `uv build`.
- Built wheel contains LICENSE metadata, no `docs.md` files, and
  `data/agent_guide.md`.
- Clean-venv install from the wheel: `mistral -h`, `mistral --version`
  (0.3.0), `mistral agent`, and `mistral ocr --dry-run --json` on a sample
  file all behave.
- A pre-upgrade dedupe index entry still matches post-upgrade (fingerprint
  compatibility test).
- CI workflow is syntactically valid and runs the same gates (first real run
  happens on push).
