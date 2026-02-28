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
2. Target `develop` for normal feature/fix PRs.
3. Use release PRs from `develop` -> `master` for stable releases.
4. Add or update tests for behavior changes.
5. Keep docs aligned with CLI behavior.
6. Ensure CI is green before requesting review.

## Branch Strategy

Use this flow to keep production stable:

- `master` (or `main`) is stable and releasable at all times.
- `develop` is the active integration branch.
- `feature/*`, `fix/*`, `chore/*` branches are created from `develop`.
- release tags (`vX.Y.Z`) must be created from `master`.

Recommended local workflow:

```bash
git checkout develop
git pull
git checkout -b feature/my-change
```

After merge to `develop`, prepare release in `master`:

```bash
git checkout master
git merge --no-ff develop
git tag vX.Y.Z
git push origin master --tags
```

## PyPI Deployment

- Primary mode: repository secret `PYPI_API_TOKEN`.
- Fallback mode: trusted publishing (OIDC) if token secret is absent.
- Release workflow is in `.github/workflows/publish.yml`.

If publish fails with `invalid-publisher`, verify PyPI trusted publisher claims:

- owner: `0x-Professor`
- repo: `PersonaPort`
- workflow: `.github/workflows/publish.yml`
- tag/ref must match the release run.

## Existing Checklist

1. Keep all safety warnings prominent.
2. Add or update tests for behavior changes.
3. Keep docs aligned with CLI behavior.
4. Ensure CI is green before requesting review.

## Release Flow

- Create/update changelog entries.
- Tag a version (`vX.Y.Z`) after merge.
- Publish workflow builds and uploads to PyPI (configured via trusted publishing).
