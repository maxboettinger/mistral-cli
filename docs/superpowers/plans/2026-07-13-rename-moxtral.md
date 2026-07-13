# Rename mistral-cli → moxtral Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the tool's public identity from `mistral-cli` / `mistral` to `moxtral` everywhere (distribution, command, import package, data dir, docs, skill), and add a trademark-safe "powered by Mistral AI" README section.

**Architecture:** Pure rename — no behavior changes. The safety net is the existing test suite (269-line strict-typed project: pytest + pyright strict + ruff). The rename splits along one principled line: names that refer to the **Mistral API/SDK/models stay** (`MISTRAL_API_KEY`, `mistralai`, `mistral_client.py`, `MistralGateway`, `mistral-ocr-latest`, "Mistral OCR", "Voxtral"); names that refer to the **tool's identity change** (dist, command, package, `~/.mistral` dir, `_MISTRAL_COMPLETE`, `MistralCliError`, temp-file prefix, schema title).

**Tech Stack:** Python 3.11+, hatchling, uv, click, pytest, pyright (strict), ruff.

## Global Constraints

- New identity, exact values: distribution `moxtral`, console script `moxtral`, import package `moxtral` (dir `src/moxtral`), data dir `~/.moxtral`, config `~/.moxtral/config.toml`, completion env var `_MOXTRAL_COMPLETE` (click derives it — never hardcoded in code), exception base `MoxtralError`, atomic-write temp prefix `.moxtral-`, NDJSON schema title `"moxtral --json output record"`.
- Version bump `0.3.0` → `0.4.0` (breaking: install name, command name, default paths). NDJSON `schema_version` stays `1` — record shapes are unchanged; the schema `title` is cosmetic metadata.
- **Do NOT touch:** `MISTRAL_API_KEY`, `mistralai` dependency, `src/*/mistral_client.py` filename, `MistralGateway`, model IDs (`mistral-ocr-latest`, `voxtral-*`), prose "Mistral OCR"/"Mistral API"/"Mistral AI", historical docs under `docs/superpowers/` (except this plan), historical CHANGELOG entries, `.recall/`.
- Work on branch `feature/rename-moxtral` off `main`.
- Every task ends green: `uv run pytest -q`, `uv run pyright`, `uv run ruff check .`, `uv run ruff format --check .` where code changed.

---

### Task 1: Rename the import package and distribution

**Files:**
- Rename: `src/mistral_cli/` → `src/moxtral/` (git mv, all contents)
- Modify: `pyproject.toml` (name, version, scripts, urls, wheel packages), `src/moxtral/__init__.py`, every `from mistral_cli...`/`import mistral_cli` in `src/` and `tests/`, `tests/test_config_cli.py` module-path assertions
- Regenerate: `uv.lock`

**Interfaces:**
- Produces: import package `moxtral`, dist `moxtral` v0.4.0, console script `moxtral = "moxtral.cli.main:cli"`. All later tasks assume `uv run moxtral` works.

- [ ] **Step 1: Branch**

```bash
git checkout -b feature/rename-moxtral
```

- [ ] **Step 2: git mv the package**

```bash
git mv src/mistral_cli src/moxtral
```

- [ ] **Step 3: Rewrite imports and module-path strings**

```bash
# macOS sed; word-boundary-safe because mistral_cli is always followed by . or " or space
grep -rl "mistral_cli" src tests | xargs sed -i '' 's/mistral_cli/moxtral/g'
```

This also fixes `tests/test_config_cli.py:230/267` (`"mistral_cli.errors.ConfigError"` → `"moxtral.errors.ConfigError"` — matches the runtime module path after the rename) and `src/moxtral/cli/agent.py:29` (`resources.files("mistral_cli")` → `resources.files("moxtral")`).

- [ ] **Step 4: pyproject.toml identity block**

```toml
[project]
name = "moxtral"
version = "0.4.0"

[project.scripts]
moxtral = "moxtral.cli.main:cli"

[project.urls]
Repository = "https://github.com/maxboettinger/moxtral"
Issues = "https://github.com/maxboettinger/moxtral/issues"
Changelog = "https://github.com/maxboettinger/moxtral/blob/main/CHANGELOG.md"

[tool.hatch.build.targets.wheel]
packages = ["src/moxtral"]
```

(Also update `description` if it says "mistral-cli"; keep the Mistral OCR/Voxtral wording.)

- [ ] **Step 5: Version lookup in `src/moxtral/__init__.py`**

```python
__version__ = version("moxtral")
```

- [ ] **Step 6: Regenerate lockfile, run gates**

```bash
uv lock
uv sync
uv run pytest -q          # expected: all pass
uv run pyright            # expected: 0 errors
uv run ruff check . && uv run ruff format --check .
uv run moxtral --help     # expected: usage with agent, config, ocr, transcribe
```

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "refactor!: rename package/distribution mistral-cli -> moxtral"
```

### Task 2: Rename tool-identity strings inside the source

**Files:**
- Modify: `src/moxtral/cli/main.py:15,28-29`, `src/moxtral/storage.py:40,155`, `src/moxtral/schema.py:124`, `src/moxtral/errors.py` (class + docstring), `src/moxtral/cli/__init__.py:1`, `tests/test_errors.py`, `tests/test_sources.py`, any other `MistralCliError` importer

**Interfaces:**
- Produces: `MoxtralError` (same shape as old `MistralCliError`), default dirs `~/.moxtral/...`. README (Task 4) documents these values.

- [ ] **Step 1: Data/config paths**

`src/moxtral/cli/main.py:15`:
```python
DEFAULT_CONFIG_PATH = Path("~/.moxtral/config.toml").expanduser()
```

`src/moxtral/storage.py:155`:
```python
Path("~/.moxtral").expanduser() if base_dir is None else base_dir
```

`src/moxtral/storage.py:40`:
```python
prefix=".moxtral-",
```

- [ ] **Step 2: Help text and schema title**

`src/moxtral/cli/main.py:28-29`: replace `'mistral agent'` → `'moxtral agent'` (both lines).

`src/moxtral/schema.py:124`:
```python
"title": "moxtral --json output record",
```

- [ ] **Step 3: Exception + docstrings**

```bash
grep -rl "MistralCliError" src tests | xargs sed -i '' 's/MistralCliError/MoxtralError/g'
```

`src/moxtral/errors.py:12` docstring → `"""Base exception for expected moxtral failures."""`
`src/moxtral/cli/__init__.py:1` → `"""Command-line interface for moxtral."""`

- [ ] **Step 4: Sweep for leftovers**

```bash
grep -rn "mistral-cli\|mistral_cli\|~/.mistral\|'mistral \|\"mistral " src tests | grep -v "MISTRAL_API_KEY\|mistralai\|mistral_client\|MistralGateway\|mistral-ocr"
```
Expected: only hits that are legitimately about the Mistral API (or none).

- [ ] **Step 5: Gates + commit**

```bash
uv run pytest -q && uv run pyright && uv run ruff check .
git add -A && git commit -m "refactor!: moxtral identity in paths, help, schema title, exception name"
```

### Task 3: Update the packaged agent guide

**Files:**
- Modify: `src/moxtral/data/agent_guide.md`

- [ ] **Step 1: Rewrite identity references**

- Title → `# moxtral CLI — agent usage guide`
- Every command example `mistral ocr|transcribe|config|agent ...` → `moxtral ...`
- `~/.mistral/ocr/`, `~/.mistral/transcriptions/` → `~/.moxtral/...`
- Line 3 intro keeps "wraps two Mistral capabilities" (API prose stays); `mistral-ocr-latest` model default stays.

```bash
sed -i '' -e 's|~/.mistral/|~/.moxtral/|g' -e 's/\bmistral \(ocr\|transcribe\|config\|agent\)/moxtral \1/g' src/moxtral/data/agent_guide.md
# then manually fix the title line and re-grep:
grep -n "mistral" src/moxtral/data/agent_guide.md   # only API/model prose may remain
```

- [ ] **Step 2: Verify via the CLI + tests, commit**

```bash
uv run moxtral agent | grep -c "moxtral"   # expected: > 0
uv run moxtral agent | grep "mistral "     # expected: no tool-command hits
uv run pytest -q tests/test_agent_cli.py
git add -A && git commit -m "docs: agent guide uses moxtral identity"
```

### Task 4: Rewrite README (identity + trademark/powered-by section)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Mechanical identity pass**

- Title `# moxtral`, badge/repo URLs → `maxboettinger/moxtral`
- All command examples `mistral ...` → `moxtral ...`
- Paths `~/.mistral/...` → `~/.moxtral/...`
- Completion block:
```bash
eval "$(_MOXTRAL_COMPLETE=zsh_source moxtral)"     # ~/.zshrc
eval "$(_MOXTRAL_COMPLETE=bash_source moxtral)"    # ~/.bashrc
_MOXTRAL_COMPLETE=fish_source moxtral | source     # ~/.config/fish/config.fish
```
- Install block:
```bash
git clone https://github.com/maxboettinger/moxtral.git
uv tool install ./moxtral
uv tool list          # the `moxtral` executable must be listed under `moxtral`
moxtral --help        # must show: agent, config, ocr, transcribe
```

- [ ] **Step 2: Replace the OpenStack-shadowing warning with a naming/trademark section**

Delete the old blockquote about `pip install mistral` shadowing (README ~lines 77–85; obsolete now that the command isn't `mistral`). Add near the top, right under the intro:

```markdown
> **Powered by [Mistral AI](https://mistral.ai)** — moxtral is an independent,
> open-source CLI built on the official Mistral AI platform APIs (Mistral OCR
> for documents, Voxtral for audio). It is **not affiliated with, endorsed by,
> or sponsored by Mistral AI**.
```

And a full section before "Development":

```markdown
## About the name & trademarks

moxtral (**M**istral **O**CR + Vo**xtral**) is an independent project. It is
not affiliated with, endorsed by, or sponsored by Mistral AI. "Mistral",
"Mistral OCR", and "Voxtral" are trademarks of their respective owner and are
used here only to identify the APIs this tool talks to.

The CLI deliberately does not claim the bare `mistral` command: that name
belongs to other projects (the OpenStack Mistral client on PyPI installs a
`mistral` executable) and plausibly to Mistral AI's own tooling in the future
(their coding agent currently installs `vibe`). `moxtral` can be installed
alongside all of them without conflict.
```

- [ ] **Step 3: Sweep + commit**

```bash
grep -n "mistral" README.md | grep -v "MISTRAL_API_KEY\|Mistral AI\|Mistral OCR\|Mistral API\|mistralai\|mistral-ocr\|OpenStack Mistral\|Voxtral"
# expected: no tool-identity hits
git add README.md && git commit -m "docs: README moxtral identity + trademark/powered-by section"
```

### Task 5: CHANGELOG entry + CLAUDE.md + docs.md sweeps

**Files:**
- Modify: `CHANGELOG.md` (new 0.4.0 entry only; history untouched), `CLAUDE.md`, `src/moxtral/docs.md`, `src/moxtral/cli/docs.md`, `src/moxtral/services/docs.md` (excluded from wheel but shipped in repo)

- [ ] **Step 1: CHANGELOG — prepend a 0.4.0 section**

```markdown
## 0.4.0 — 2026-07-13

### Changed (breaking)

- **Renamed the project `mistral-cli` → `moxtral`.** The distribution,
  console script (`mistral` → `moxtral`), import package
  (`mistral_cli` → `moxtral`), default data dir (`~/.mistral` → `~/.moxtral`),
  and shell-completion variable (`_MISTRAL_COMPLETE` → `_MOXTRAL_COMPLETE`)
  all change. Avoids the `mistral` executable collision (OpenStack
  Mistral client) and any implication of being an official Mistral AI tool.
  Migrate with `mv ~/.mistral ~/.moxtral`. NDJSON record shapes, error
  codes, and exit codes are unchanged (`schema_version` stays 1).
```

- [ ] **Step 2: CLAUDE.md** — update commands (`uv run moxtral --help`, `--cov=moxtral`), entry point `moxtral.cli.main:cli`, "A `moxtral` CLI wrapping..." intro, `~/.moxtral/...` paths in the storage/dedupe/config rows, `mistral agent` mentions → `moxtral agent`, `src/mistral_cli/data/agent_guide.md` → `src/moxtral/data/agent_guide.md`.

- [ ] **Step 3: docs.md files** — same identity sweep (`mistral_cli` module refs → `moxtral`, command examples, paths).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: changelog 0.4.0 rename entry; CLAUDE.md + module docs identity"
```

### Task 6: Full verification + merge

- [ ] **Step 1: All gates**

```bash
uv run pytest -q --cov=moxtral --cov-report=term-missing
uv run pyright
uv run ruff check . && uv run ruff format --check .
uv lock --check && uv build
```

- [ ] **Step 2: End-to-end smoke (no API key needed)**

```bash
uv run moxtral --help
uv run moxtral agent | head -3
uv run moxtral agent --schema | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['title'])"
# expected: "moxtral --json output record"
uv run moxtral ocr --dry-run --json --quiet README.md   # dry-run NDJSON, no key required
echo "exit: $?"
```

- [ ] **Step 3: Leftover sweep of the whole repo**

```bash
grep -rn "mistral_cli\|_MISTRAL_COMPLETE" --include="*.py" --include="*.toml" --include="*.md" . | grep -v ".venv\|.recall\|docs/superpowers\|CHANGELOG"
# expected: empty
```

- [ ] **Step 4: Merge to main**

```bash
git checkout main && git merge --no-ff feature/rename-moxtral -m "Merge feature/rename-moxtral: rename mistral-cli -> moxtral (0.4.0)"
git branch -d feature/rename-moxtral
```

(Push only after Max renames the GitHub repo — see manual steps.)

### Task 7: Update the local Claude skill

**Files:**
- Rename: `~/.claude/skills/using-mistral-cli/` → `~/.claude/skills/using-moxtral/`
- Modify: its `SKILL.md` — frontmatter `name: using-moxtral`, description keeps OCR/Voxtral keywords but says "the `moxtral` CLI"; body: all `mistral <cmd>` → `moxtral <cmd>`, `~/.mistral/` → `~/.moxtral/`, heading "Using moxtral".

- [ ] **Step 1: mv the directory, sed the identity strings, manually fix frontmatter name/description**
- [ ] **Step 2: Re-grep for stale `mistral ` command refs** (API prose like "Mistral OCR" stays)

### Manual steps for Max (cannot be automated from here)

1. **Rename the GitHub repo:** github.com/maxboettinger/mistral-cli → Settings → General → Repository name → `moxtral`. GitHub redirects the old URL automatically.
2. **Update the local remote:** `git remote set-url origin git@github.com:maxboettinger/moxtral.git` (then push).
3. **Migrate local data:** `mv ~/.mistral ~/.moxtral` (keeps config, dedupe index, saved results).
4. **Reinstall the tool:** `uv tool uninstall mistral-cli && uv tool install ~/github/mistral-cli` (and optionally rename the local clone dir).
5. **Shell completion:** replace the `_MISTRAL_COMPLETE=zsh_source mistral` line in `~/.zshrc` with `eval "$(_MOXTRAL_COMPLETE=zsh_source moxtral)"`.
