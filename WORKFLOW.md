---
tracker:
  kind: github
  labels:
    ready: agent-ready
    running: agent-running
    handoff: human-review
    rework: agent-rework
    blocked: blocked
polling:
  interval_seconds: 30
workspace:
  root: .symphony/workspaces
  logs_root: .symphony/logs
  base_branch: develop
hooks:
  after_create: |
    python -c "import os, pathlib, subprocess, sys; venv_python = pathlib.Path('.venv') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'); subprocess.run([sys.executable, '-m', 'venv', '.venv'], check=True); subprocess.run([str(venv_python), '-m', 'pip', 'install', '-e', '.[dev]'], check=True)"
  timeout_seconds: 1800
validation:
  commands:
    - ruff check .
    - pytest
agent:
  max_concurrent_agents: 1
  retry_backoff_seconds: 10
  max_retry_backoff_seconds: 300
pull_request:
  auto_merge: true
  merge_method: squash
  delete_branch: true
  checks_timeout_seconds: 1800
  checks_poll_seconds: 15
  high_risk_paths:
    - personaport/browser/
    - personaport/config.py
    - personaport/db.py
    - personaport/llm.py
codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: false
    writableRoots: []
  turn_timeout_seconds: 1800
  read_timeout_seconds: 5
---

You are working on GitHub issue `#{{ issue.number }}` for PersonaPort.

Read these repo-local docs before editing:

- `docs/agents/repo-map.md`
- `docs/agents/validation.md`
- `docs/agents/safety.md`

Primary task context:

- Title: `{{ issue.title }}`
- Body:
{{ issue.body or "(no issue body provided)" }}
- Attempt: `{{ attempt }}`

Repo rules that must survive every change:

1. Preserve warning language in the CLI and README.
2. Never log, hard-code, or persist raw passwords, tokens, or provider secrets.
3. Keep PersonaPort local-first by default.
4. Prefer official export paths first; keep scraping and risky automation behind explicit confirmation.
5. Target `develop` for all feature and fix work.
6. Update docs when CLI behavior, safety guarantees, or platform support changes.

Implementation guidance:

- Make the smallest coherent change that closes the issue.
- Prefer straightforward Python and readable tests over clever abstractions.
- Add or update tests for every behavioral change.
- Keep edits inside the assigned worktree.
- Do not create commits, tags, pull requests, or issue comments yourself; the harness handles those steps.

Definition of done for handoff:

- The code is consistent with the repo docs above.
- `ruff check .` and `pytest` are expected to pass locally after your changes.
- If browser automation behavior changes, call that out clearly in your final summary.

Merge rules:

- The harness creates the PR first, then performs a maintainer review.
- Low-risk PRs can be merged automatically only after local validation passes and GitHub checks finish successfully.
- Any change touching browser automation, auth/session handling, provider key handling, or other high-risk paths must stop at human review even if CI passes.
