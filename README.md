# PersonaPort

`PersonaPort` is a local-first Python CLI for moving conversation context and persona data between AI platforms.

## WARNING (READ BEFORE USING)

**THIS TOOL USES BROWSER AUTOMATION WHICH MAY VIOLATE PLATFORM TOS. USE AT YOUR OWN RISK. RISK OF ACCOUNT BAN. WE STRONGLY RECOMMEND USING OFFICIAL MANUAL EXPORT INSTEAD.**

- No passwords are stored in code or logs.
- Sessions are saved as Playwright storage state after manual login.
- Data stays on your machine (`~/.personaport` by default).
- Use `--safe-mode` / `--no-scrape` for official export-only workflows.

## What It Does

1. Opens source platform in a visible browser and reuses saved session.
1. Triggers official export flow (safe mode) or fallback scraping (unsafe mode).
1. Normalizes history into a neutral schema.
1. Extracts persona and compresses history into a compact migration map (topics, tasks, goals, priority threads).
1. Uses LiteLLM provider routing with local-first fallback (`ollama` first, then hosted providers if configured).
1. Opens target platform and injects migration prompt + optional knowledge files only after interactive confirmation.

## Supported Platforms

- Source: `chatgpt`, `claude`, `gemini`
- Target: `chatgpt`, `claude`, `gemini`

`gemini` is intentionally conservative in v0.1 (manual guidance + basic automation paths).

## Install (PyPI)

```bash
python -m pip install --upgrade pip
python -m pip install personaport
playwright install chromium
```

## Install (Development)

```bash
git clone https://github.com/0x-Professor/PersonaPort.git
cd PersonaPort
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
playwright install chromium
```

## Repository Structure

```text
PersonaPort/
  .github/
    ISSUE_TEMPLATE/
    workflows/
      ci.yml
      publish.yml
  personaport/
    browser/
      platforms/
    templates/
    utils/
    cli.py
    config.py
    db.py
    models.py
    processor.py
    transfer.py
  tests/
  CONTRIBUTING.md
  Makefile
  pyproject.toml
  README.md
```

## Quick Start

```bash
# 1) login once per platform (manual login in opened browser)
personaport login --platform chatgpt
personaport login --platform claude

# 2) safe export + process + migrate package output
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape

# 2a) configure provider key once (stored in OS keyring)
personaport provider set-key --provider groq

# 2b) process using a selected provider/model for persona + summarization
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt \
  --target claude --all --llm-provider groq --model openai/gpt-oss-20b

# 2b) safe export but continue automatically once you download ZIP from email link
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape --wait-for-export 10

# 2c) provide the downloaded export directly (best for ChatGPT email exports)
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape --export-file ~/Downloads/chatgpt_export.zip

# 2d) same flow for Claude export ZIP
personaport export --from claude --to chatgpt --all --safe-mode --no-scrape --export-file ~/Downloads/claude_export.zip

# 3) process manual export file directly
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt

# 3b) process the whole export into one merged migration bundle
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt --target claude --all

# 4) migrate a cached session or export file to target platform
personaport migrate --input session --target claude

# 4b) migrate whole cached history (all conversations) to target platform
personaport migrate --input session --source chatgpt --target claude --all
```

## CLI Commands

- `personaport login --platform <chatgpt|claude|gemini>`
- `personaport export --from <platform> --to <platform> --all [--safe-mode] [--no-scrape] [--export-file path] [--wait-for-export N]`
- `personaport process --file <export.zip|json> [--from platform] [--all] [--persona "..."] [--llm-provider ...] [--model ...]`
- `personaport migrate --input <session|conversation_id|file> --target <platform> [--all] [--llm-provider ...] [--model ...]`
- `personaport provider list`
- `personaport provider set-key --provider <name>`
- `personaport provider delete-key --provider <name>`

Run `personaport --help` for global options.

When `--export-file` is provided, PersonaPort parses the file directly and does not require a source-platform browser session.

## Safe Mode

- `--safe-mode`: Only official export actions, no scraping fallback.
- `--no-scrape`: Disable scraping even in unsafe mode.
- `--unsafe-mode`: Enables fallback scraping and risky flows, requires confirmation.

## Local Data Layout

```text
~/.personaport/
  config.yaml
  personaport.db
  sessions/
    chatgpt_state.json
    claude_state.json
    gemini_state.json
  exports/
  processed/
```

## Supported Export Shapes

- ChatGPT ZIP with sharded files like `conversations-000.json` ... `conversations-XYZ.json` (all shards are aggregated).
- ChatGPT `chat.html` fallback exports.
- Claude ZIP with `conversations.json`, including attachment-only messages (uses `extracted_content` when text is empty).
- Large knowledge payloads are chunked into multiple files for target upload when needed.
- Migration output is compact by default: no full-history dump in upload text; full normalized data remains in local JSON artifacts.

## LLM Providers and Keys

Supported summarization/persona providers:

- `ollama` (local, no key)
- `groq`
- `openrouter`
- `openai`
- `anthropic`
- `gemini`
- `together`

Per-run key injection:

```bash
personaport process --file export.zip --llm-provider groq --api-key "<key>"
```

Provider + model routing example (explicit provider, model name, and temporary key):

```bash
personaport process --file export.zip --from chatgpt --target claude \
  --llm-provider groq --model openai/gpt-oss-20b --api-key "<groq-key>"
```

You can also pass custom providers not pre-listed (for LiteLLM-compatible routes):

```bash
personaport process --file export.zip --llm-provider myprovider --model myprovider/my-model
```

Persist key in OS keyring:

```bash
personaport provider set-key --provider groq
personaport process --file export.zip --llm-provider groq --model openai/gpt-oss-20b
```

Fallback order when model/provider fails:

1. user-selected model
2. provider default model
3. configured default model (`config.yaml`)
4. built-in chain (`ollama/llama3.1:8b`, `groq/openai/gpt-oss-20b`, `openrouter/...:free`)

## Security Notes

- Credentials are never hard-coded.
- Secrets can be stored via OS keyring.
- Session state files may contain auth tokens. Protect your local machine and backups.
- If unsure, revoke sessions from the source platform and regenerate.

## How It Works Internally

- `Typer + Rich` for CLI UX, warnings, progress, tables.
- `Playwright` for visible automation (`headless=False` default).
- `sqlite3` cache for normalized conversations and processed bundles.
- `LiteLLM` for optional persona extraction + summarization (`ollama/...` default model).
- `Jinja2` templates for target-specific migration prompts.

## Tests

```bash
pytest
```

## CI and Lint

GitHub Actions runs on every push and pull request to `develop`, `master`, and `main`:

- `ruff check .`
- `pytest`

Run the same checks locally:

```bash
make check
```

## Package Publishing

Publishing is configured in `.github/workflows/publish.yml`:

- Trigger: GitHub Release published.
- Build: `python -m build` and `python -m twine check dist/*`.
- Publish: `pypa/gh-action-pypi-publish` with either:
  - `PYPI_API_TOKEN` repository secret (token mode), or
  - PyPI Trusted Publishing (OIDC mode, when token secret is not set).

Setup steps:

1. Create the `personaport` project on PyPI.
2. Choose one publishing mode:
   - Token mode: add repository secret `PYPI_API_TOKEN` (recommended for quick setup).
   - Trusted mode: configure PyPI Trusted Publisher for:
     - Owner: `0x-Professor`
     - Repository: `PersonaPort`
     - Workflow: `.github/workflows/publish.yml`
     - Environment: not required
3. Publish a GitHub release tag (for example `v0.1.1`).

Trusted publishing troubleshooting:

- If you see `invalid-publisher` in publish logs, PyPI trusted publisher claims do not match.
- Verify the repository/workflow/ref in PyPI exactly match the GitHub run claims.
- Reference: https://docs.pypi.org/trusted-publishers/troubleshooting/

## Branching Model

- `master` (or `main`): stable production branch.
- `develop`: integration branch for ongoing development.
- feature branches: branch from `develop`, open PR into `develop`.
- release PRs: merge `develop` into `master` after validation.
- version tags/releases are cut from `master`.

## Development Status

v0.1 focuses on safe-mode flows first, then gated risky automation behind explicit confirmation.
