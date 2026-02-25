# Contributing to PersonaPort

## Safety First

Before submitting changes, preserve these non-negotiable guarantees:

- Keep all data local by default.
- Never hard-code, log, or persist raw passwords.
- Keep warning messaging prominent in README and CLI output.
- Prefer official export flows first; gate risky scraping behind explicit user confirmation.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
playwright install chromium
```

## Development Commands

```bash
make lint
make test
make check
```

If `make` is unavailable on your platform, run:

```bash
ruff check .
pytest
```

## Pull Requests

1. Create a focused branch per change.
2. Add or update tests for behavior changes.
3. Keep docs aligned with CLI behavior.
4. Ensure CI is green before requesting review.

## Release Flow

- Create/update changelog entries.
- Tag a version (`vX.Y.Z`) after merge.
- Publish workflow builds and uploads to PyPI (configured via trusted publishing).
