# Noridoc: cli

Path: @/src/mistral_cli/cli

### Overview

The Click command layer and the outermost entry point of the `mistral` console
script (`mistral_cli.cli.main:cli`). It defines the root group plus the `ocr`,
`transcribe`, and `config` commands, wires together sources, services, gateways,
and storage for each source, and owns all user-facing terminal output and its
secret-redaction boundary.

### How it fits into the larger codebase

This is the top of the dependency graph: command modules import
[`sources.py`](../sources.py), [`models.py`](../models.py),
[`services/`](../services/docs.md), [`mistral_client.py`](../mistral_client.py),
[`formatters.py`](../formatters.py), [`storage.py`](../storage.py), and
[`config.py`](../config.py), assembling them into the request→service→client→
storage flow. Everything the user sees on stdout/stderr passes through the
[`ConsoleBundle`](../console.py) held on the shared `AppContext`. The
`create_gateway` / `create_result_store` factory functions in `ocr.py` and
`transcribe.py` are the seams tests patch to inject fakes.

### Core Implementation

`main.py` defines the root `cli` group with `--version`, `--debug`, and
`--config PATH`. It stores a frozen `AppContext` (config path, debug flag, and a
`ConsoleBundle`) on `ctx.obj`, which every subcommand receives via
`@click.pass_obj`.

`ocr.py` and `transcribe.py` are near-mirrors. Each declares its Click options,
converts sentinel choices (e.g. OCR `--table-format inline` / `--confidence none`
become `None`), then loops over `SOURCE...` values sequentially:

```
resolve_source ─▶ build_*_request ─▶ [lazily build _Runtime: resolve api key,
   create service+gateway+store] ─▶ service.run ─▶ redact_result ─▶
   format_*_markdown ─▶ store.save ─▶ print "Saved:" paths (stderr)
```

The `_Runtime` (secrets, service, store) is created lazily on the first source
that survives validation, so a batch of only-invalid sources never resolves an
API key. A failure on one source is reported and counted; the loop continues.
A final `Summary: N succeeded, M failed.` prints to stderr, and any failure
raises `click.exceptions.Exit(1)`.

`config.py` defines the `config` group (`set`, `show`, `unset`, `path`).
`config set api-key` reads the secret only from a hidden confirmed prompt or
`--stdin` — never from an argument; `allow_extra_args`/`ignore_unknown_options`
plus an explicit `context.args` check reject any positional value so a key can
never land in process arguments or shell history.

`common.py` holds the shared helpers all commands funnel through:
`candidate_secrets` (gathers env + configured keys to redact), `resolve_api_key`,
`redact_result` (recursively scrubs an `ApiResult`), `report_error` (translates
via [`errors.py`](../errors.py) and writes safe output), and `safe_terminal_text`
(sanitize control sequences + redact).

### Things to Know

Secret redaction is a strict invariant enforced here, not in the services. The
`ApiResult` is passed through `redact_result` *before* it is formatted, saved, or
echoed, so the persisted Markdown/JSON and the terminal both receive
already-scrubbed content; `safe_terminal_text` is applied again on every stderr/
stdout write as a second layer that also strips terminal control sequences.
`candidate_secrets` deliberately loads the configured key with `environ={}` and
swallows `ConfigError` so that a malformed config file cannot mask the real input
error the user is trying to diagnose. Progress, saved paths, summaries, and all
errors go to stderr; only `--stdout` Markdown goes to stdout, keeping piped
Markdown clean, with multiple documents separated by a `\n\n---\n\n` rule. The
root `--debug` flag causes `report_error` to additionally emit a redacted
traceback via [`errors.format_debug_exception`](../errors.py).

Created and maintained by Nori.
