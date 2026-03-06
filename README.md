# PersonaPort

[![PyPI](https://img.shields.io/pypi/v/personaport.svg)](https://pypi.org/project/personaport/)
[![CI](https://github.com/0x-Professor/PersonaPort/actions/workflows/ci.yml/badge.svg)](https://github.com/0x-Professor/PersonaPort/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/personaport.svg)](https://pypi.org/project/personaport/)

`PersonaPort` is a local-first Python CLI for moving conversation context and persona data between AI platforms.

## Warning

**THIS TOOL USES BROWSER AUTOMATION WHICH MAY VIOLATE PLATFORM TOS. USE AT YOUR OWN RISK. RISK OF ACCOUNT BAN. WE STRONGLY RECOMMEND OFFICIAL MANUAL EXPORT WHEN POSSIBLE.**

- No passwords are stored in code or logs.
- Sessions are saved as Playwright storage-state files after manual login.
- Data stays local (`~/.personaport` by default).
- `--safe-mode` and `--no-scrape` keep flows on official export paths.

## Supported Platforms

- Source: `chatgpt`, `claude`, `gemini` (experimental)
- Target: `chatgpt`, `claude`, `gemini` (experimental)

`gemini` is **[Experimental]** in v0.1 and may require manual steps.

## Install

### PyPI

```bash
python -m pip install --upgrade pip
python -m pip install personaport
personaport install-deps
```

### Development

```bash
git clone https://github.com/0x-Professor/PersonaPort.git
cd PersonaPort
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
personaport install-deps
```

## First Run

```bash
personaport init
personaport login --platform chatgpt
personaport login --platform claude
```

`personaport init` creates local config and checks required browser dependencies.

## Demo

![PersonaPort terminal demo](https://raw.githubusercontent.com/0x-Professor/PersonaPort/master/docs/images/terminal-demo.svg)

Short terminal walkthrough:

1. `personaport login --platform chatgpt`
2. `personaport export --from chatgpt --to claude --safe-mode --no-scrape`
3. `personaport migrate --input session --target claude --no-auto-inject`

## Quick Start

```bash
# 1) login once per platform
personaport login --platform chatgpt
personaport login --platform claude

# 2) safe export + process + migrate package output
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape

# 2a) configure provider key once (stored in OS keyring)
personaport provider set-key --provider groq

# 2b) process using a selected provider/model for persona + summarization
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt \
  --target claude --all --llm-provider groq --model groq/llama-3.1-8b-instant

# 2c) safe export and wait 10 minutes for manual ZIP download
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape --wait-for-export 10

# 2d) provide the downloaded export directly (ChatGPT email ZIP)
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape --export-file ~/Downloads/chatgpt_export.zip

# 2e) same flow for Claude export ZIP
personaport export --from claude --to chatgpt --all --safe-mode --no-scrape --export-file ~/Downloads/claude_export.zip

# 3) process manual export file directly
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt

# 3b) process the whole export into one merged migration bundle
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt --target claude --all

# 4) migrate a cached session or export file to target platform
personaport migrate --input session --target claude --no-auto-inject

# 4b) migrate whole cached history to target platform
personaport migrate --input session --source chatgpt --target claude --all --no-auto-inject
```

## CLI Commands

- `personaport --version`
- `personaport version`
- `personaport init`
- `personaport install-deps`
- `personaport login --platform <chatgpt|claude|gemini>`
- `personaport logout --platform <chatgpt|claude|gemini>`
- `personaport logout --all`
- `personaport export --from <platform> --to <platform> --all [--safe-mode] [--no-scrape] [--export-file path] [--wait-for-export N_minutes] [--no-remote-llm]`
- `personaport process --file <export.zip|json|html> [--from platform] [--all] [--persona "..."] [--llm-provider ...] [--model ...] [--no-remote-llm]`
- `personaport migrate --input <session|conversation_id|file> --target <platform> [--all] [--llm-provider ...] [--model ...] [--no-remote-llm]`
- `personaport provider list`
- `personaport provider set-key --provider <name>`
- `personaport provider delete-key --provider <name>`

Run `personaport --help` for full options.

## LLM Providers and Models

Supported providers:

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

Provider + model example:

```bash
personaport process --file export.zip --from chatgpt --target claude \
  --llm-provider groq --model groq/llama-3.1-8b-instant --api-key "<groq-key>"
```

Persist key in OS keyring:

```bash
personaport provider set-key --provider groq
personaport process --file export.zip --llm-provider groq --model groq/llama-3.1-8b-instant
```

Fallback order when model/provider fails:

1. user-selected model
2. provider default model
3. configured default model (`config.yaml`)
4. built-in chain (`ollama/llama3.1:8b`, `groq/llama-3.1-8b-instant`, `openrouter/...:free`)

### Remote Fallback Safety

- Default behavior prefers local (`ollama`) first.
- If local fails and remote fallback is possible, PersonaPort prompts before sending data to hosted providers.
- `--no-remote-llm` hard-blocks remote fallback.

## Persona Override (`--persona`)

`--persona` lets you manually set the system prompt used in output artifacts.

```bash
personaport process --file export.zip --from chatgpt \
  --persona "Act as my long-term coding copilot. Be concise, practical, and task-focused."
```

Use a single natural-language instruction string. It is injected as the persona system prompt.

## Safe Mode

- `--safe-mode`: official export actions only.
- `--no-scrape`: disable scraping even in unsafe mode.
- `--unsafe-mode`: allows fallback scraping and risky automation (with confirmation).

## Supported Export Shapes

- ChatGPT ZIP with sharded `conversations-000.json` ... `conversations-XYZ.json` (all shards aggregated).
- ChatGPT `chat.html` fallback export.
- Claude ZIP with `conversations.json`, including attachment-only messages (`extracted_content` supported).

### ChatGPT `chat.html` Fallback Usage

```bash
personaport process --file ~/Downloads/chat.html --from chatgpt
```

`chat.html` can come from ChatGPT's export package; if ZIP parsing cannot recover JSON conversations, PersonaPort attempts the HTML fallback parser.

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

Session files contain auth tokens. PersonaPort attempts to set restrictive permissions on saved session files. Use `personaport logout` to remove them.

## Migration Outputs

Each run writes:

- `<prefix>.md` prompt markdown for target platform
- `<prefix>_knowledge.txt` compact context payload
- `<prefix>_full_json.json` normalized full artifact

Example success output (CLI table):

```text
Generated Output Files
- prompt_markdown: ~/.personaport/processed/migrate_to_claude.md
- knowledge_text:  ~/.personaport/processed/migrate_to_claude_knowledge.txt
- full_json:       ~/.personaport/processed/migrate_to_claude_full_json.json
```

## Large Histories and Chunking

If knowledge text exceeds upload size thresholds, PersonaPort auto-splits into multiple files.

- Chunk limit: `4,000,000` bytes per knowledge file.
- Output names: `..._knowledge_part001.txt`, `..._knowledge_part002.txt`, etc.
- Auto-injection attempts upload of all generated chunk files.

## Versioning and Changelog

- Run `personaport --version` to check installed version.
- Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Tests and Local Checks

```bash
pytest
make check
```

`make check` runs:

- `ruff check .`
- `pytest`

The current test suite is primarily unit-level (parsers, processor logic, DB/config helpers, transfer chunking).

## Internal Symphony Runner

PersonaPort now includes an internal-only GitHub-native Symphony runner under `tools/symphony/`.

- Repo contract: `WORKFLOW.md`
- Agent knowledge docs: `docs/agents/repo-map.md`, `docs/agents/validation.md`, `docs/agents/safety.md`
- Entry point: `python -m tools.symphony [WORKFLOW.md]`

One-shot dry scheduler pass:

```bash
python -m tools.symphony --once
```

This runner is for repository maintenance workflows only. It is not part of the published `personaport` package.
It can open and merge low-risk PRs only after maintainer review, passing local validation, and passing GitHub checks. High-risk browser/auth/session/provider-key changes still stop for human review.

## Development Dependencies

`pyproject.toml` is the source of truth for dependencies.

`requirements.txt` is a compatibility snapshot for environments that prefer `pip install -r`; keep it aligned with `pyproject.toml` when updating deps.

## Platform Support

- Linux: supported
- macOS: supported
- Windows: supported (PowerShell + visible Playwright sessions are tested paths)

## Branching Model

- `master` (or `main`): stable production branch.
- `develop`: integration branch for ongoing development.
- feature branches: branch from `develop`, open PR into `develop`.
- release PRs: merge `develop` into `master` after validation.
- version tags/releases are cut from `master`.

## Development Status

v0.1 focuses on safe-mode flows first, with risky automation behind explicit confirmation gates.
