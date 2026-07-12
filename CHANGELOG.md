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
