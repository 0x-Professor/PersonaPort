# Validation

Default validation commands for this repo:

- `ruff check .`
- `pytest`

Expected supporting checks:

- Keep README and CLI help text aligned when behavior changes.
- Keep warning text prominent for risky automation.
- Prefer unit tests for parser, config, and transfer logic.

When browser-facing behavior changes:

- Review the impacted adapter under `personaport/browser/platforms/`.
- Call out any manual verification gaps in the handoff summary.
- Do not assume browser changes are safe for auto-merge.

When no code changes are required:

- Say so explicitly in the handoff summary instead of manufacturing a diff.

Auto-merge gate:

- Passing `ruff check .` and `pytest` is necessary but not sufficient.
- The maintainer review layer also checks changed paths and GitHub PR status before merge.
