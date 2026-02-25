# PersonaPort

`PersonaPort` is a local-first Python CLI for moving conversation context and persona data between AI platforms.

## WARNING (READ BEFORE USING)

**THIS TOOL USES BROWSER AUTOMATION WHICH MAY VIOLATE PLATFORM TOS. USE AT YOUR OWN RISK. RISK OF ACCOUNT BAN. WE STRONGLY RECOMMEND USING OFFICIAL MANUAL EXPORT INSTEAD.**

**THIS TOOL USES BROWSER AUTOMATION WHICH MAY VIOLATE PLATFORM TOS. USE AT YOUR OWN RISK. RISK OF ACCOUNT BAN. WE STRONGLY RECOMMEND USING OFFICIAL MANUAL EXPORT INSTEAD.**

- No passwords are stored in code or logs.
- Sessions are saved as Playwright storage state after manual login.
- Data stays on your machine (`~/.personaport` by default).
- Use `--safe-mode` / `--no-scrape` for official export-only workflows.

## What It Does

1. Opens source platform in a visible browser and reuses saved session.
1. Triggers official export flow (safe mode) or fallback scraping (unsafe mode).
1. Normalizes history into a neutral schema.
1. Extracts persona and optionally summarizes long threads using LiteLLM (Ollama-friendly).
1. Opens target platform and injects migration prompt + optional knowledge file.

## Supported Platforms

- Source: `chatgpt`, `claude`, `gemini`
- Target: `chatgpt`, `claude`, `gemini`

`gemini` is intentionally conservative in v0.1 (manual guidance + basic automation paths).

## Install

```bash
git clone https://github.com/0x-Professor/PersonaPort.git
cd personaport
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium
```

## Quick Start

```bash
# 1) login once per platform (manual login in opened browser)
personaport login --platform chatgpt
personaport login --platform claude

# 2) safe export + process + migrate package output
personaport export --from chatgpt --to claude --all --safe-mode --no-scrape

# 3) process manual export file directly
personaport process --file ~/Downloads/chatgpt_export.zip --from chatgpt

# 4) migrate a cached session or export file to target platform
personaport migrate --input session --target claude
```

## CLI Commands

- `personaport login --platform <chatgpt|claude|gemini>`
- `personaport export --from <platform> --to <platform> --all [--safe-mode] [--no-scrape]`
- `personaport process --file <export.zip|json> [--from platform] [--persona "..."]`
- `personaport migrate --input <session|conversation_id|file> --target <platform>`

Run `personaport --help` for global options.

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

## Development Status

v0.1 focuses on safe-mode flows first, then gated risky automation behind explicit confirmation.
