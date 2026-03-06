# Repo Map

PersonaPort is a small Python CLI package with three main areas:

- `personaport/cli.py`: Typer entrypoint and command wiring.
- `personaport/processor.py`: import, parsing, persona extraction, summarization, and export processing.
- `personaport/transfer.py`: migration artifact rendering and target-platform injection flow.

Supporting modules:

- `personaport/browser/`: Playwright-backed platform adapters and session handling.
- `personaport/config.py` and `personaport/db.py`: local config, keyring integration, and cache storage.
- `personaport/llm.py`: provider defaults, key env vars, and model fallback logic.
- `personaport/templates/`: target-platform prompt templates.

Non-package repo surfaces:

- `README.md`: user-facing behavior and safety messaging.
- `CONTRIBUTING.md`: branch strategy and contributor rules.
- `.github/workflows/ci.yml`: required lint and test checks.

Internal automation:

- `WORKFLOW.md`: repo-owned machine contract for the Symphony runner.
- `tools/symphony/`: internal GitHub-native orchestration tooling. It is not part of the published package.
