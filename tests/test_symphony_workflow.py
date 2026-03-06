from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.symphony.workflow import WorkflowLoadError, WorkflowManager, load_workflow, render_prompt


def test_load_workflow_parses_front_matter_and_defaults(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
tracker:
  kind: github
workspace:
  root: workspaces
validation:
  commands:
    - ruff check .
---
Issue {{ issue.number }}: {{ issue.title }}
""".strip(),
        encoding="utf-8",
    )

    workflow = load_workflow(workflow_path)

    assert workflow.tracker.kind == "github"
    assert workflow.tracker.labels.ready == "agent-ready"
    assert workflow.workspace.root == (tmp_path / "workspaces").resolve()
    assert workflow.validation.commands == ("ruff check .",)
    assert workflow.pull_request.auto_merge is False
    assert workflow.pull_request.merge_method == "squash"


def test_load_workflow_rejects_non_mapping_front_matter(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
- invalid
---
text
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowLoadError):
        load_workflow(workflow_path)


def test_render_prompt_uses_strict_variables(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
tracker:
  kind: github
---
{{ issue.title }} / {{ missing_name }}
""".strip(),
        encoding="utf-8",
    )
    workflow = load_workflow(workflow_path)

    with pytest.raises(WorkflowLoadError):
        render_prompt(
            workflow,
            issue=type("Issue", (), {"title": "Test", "number": 1})(),  # pragma: no cover - helper object
        )


def test_workflow_manager_reload_if_changed(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
tracker:
  kind: github
polling:
  interval_seconds: 30
---
first
""".strip(),
        encoding="utf-8",
    )
    manager = WorkflowManager(workflow_path)
    workflow_path.write_text(
        """
---
tracker:
  kind: github
polling:
  interval_seconds: 45
---
second
""".strip(),
        encoding="utf-8",
    )
    next_mtime = manager._mtime_ns + 1_000_000
    os.utime(workflow_path, ns=(next_mtime, next_mtime))

    updated = manager.reload_if_changed()

    assert updated is not None
    assert updated.polling.interval_seconds == 45
    assert updated.prompt_template == "second"


def test_load_workflow_parses_pull_request_config(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
tracker:
  kind: github
pull_request:
  auto_merge: true
  merge_method: rebase
  checks_timeout_seconds: 600
  checks_poll_seconds: 5
  high_risk_paths:
    - secret/
---
Issue {{ issue.number }}
""".strip(),
        encoding="utf-8",
    )

    workflow = load_workflow(workflow_path)

    assert workflow.pull_request.auto_merge is True
    assert workflow.pull_request.merge_method == "rebase"
    assert workflow.pull_request.checks_timeout_seconds == 600
    assert workflow.pull_request.checks_poll_seconds == 5
    assert workflow.pull_request.high_risk_paths == ("secret/",)


def test_load_workflow_handles_null_workspace_paths(tmp_path: Path) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        """
---
tracker:
  kind: github
workspace:
  root:
  logs_root:
---
Issue {{ issue.number }}
""".strip(),
        encoding="utf-8",
    )

    workflow = load_workflow(workflow_path)

    assert workflow.workspace.root == (tmp_path / ".symphony" / "workspaces").resolve()
    assert workflow.workspace.logs_root is None
