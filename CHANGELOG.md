# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and Semantic Versioning.

## [Unreleased]

## [0.1.1] - 2026-02-28

### Added
- `personaport --version` global flag.
- `personaport init` guided setup command.
- `personaport install-deps` command for Playwright Chromium setup.
- `personaport logout --platform <name>` and `personaport logout --all`.
- `--no-remote-llm` option for `export`, `process`, and `migrate`.
- DB schema version metadata table with startup schema check.

### Changed
- Updated Groq defaults/examples to `groq/llama-3.1-8b-instant`.
- Added explicit prompt before remote LLM fallback from local-first mode.
- Added Chromium dependency preflight checks before browser actions.
- Applied restrictive file permissions to saved session state files when possible.
- Improved README with first-run flow, output examples, export docs, and support notes.

### Fixed
- JSON exports with UTF-8 BOM now parse correctly.
- Quick Start numbering in README.

## [0.1.0] - 2026-02-28

### Added
- Initial alpha release.
- Local-first CLI for exporting, processing, and migrating conversation context/persona data.
- ChatGPT, Claude, and Gemini platform adapters (Gemini experimental).
- Safe-mode export workflow with optional browser automation.
- SQLite-backed cache and artifact generation pipeline.
