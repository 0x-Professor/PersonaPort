from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.symphony.models import ExecutionOutcome, GitHubIssue
from tools.symphony.service import IssueExecutor, SymphonyService, _build_maintainer_review
from tools.symphony.workflow import load_workflow
from tools.symphony.worktree import GitWorktreeManager

from .symphony_test_helpers import FakeRunner


class _TrackerStub:
    def __init__(self, issue: GitHubIssue, *, claimed: bool = True) -> None:
        self.issue = issue
        self.claimed = claimed
        self.failed: list[tuple[int, str]] = []
        self.released: list[int] = []

    def get_issue(self, number: int) -> GitHubIssue:
        assert number == self.issue.number
        return self.issue

    def is_issue_still_claimed(self, issue: GitHubIssue) -> bool:
        assert issue.number == self.issue.number
        return self.claimed

    def fail_issue(self, number: int, *, proof_markdown: str) -> None:
        self.failed.append((number, proof_markdown))

    def release_issue(self, number: int) -> None:
        self.released.append(number)


def _workflow_file(tmp_path: Path) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        """
---
tracker:
  kind: github
workspace:
  root: .symphony/workspaces
---
Issue {{ issue.number }}
""".strip(),
        encoding="utf-8",
    )
    return path


def _automerge_workflow_file(tmp_path: Path) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        """
---
tracker:
  kind: github
workspace:
  root: .symphony/workspaces
pull_request:
  auto_merge: true
  checks_timeout_seconds: 60
  checks_poll_seconds: 1
---
Issue {{ issue.number }}
""".strip(),
        encoding="utf-8",
    )
    return path


def _issue() -> GitHubIssue:
    return GitHubIssue(
        number=7,
        title="Agent issue",
        body="",
        state="OPEN",
        url="https://example.test/issues/7",
        labels=frozenset({"agent-running"}),
    )


def test_service_schedules_retry_with_backoff(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_command_result("git rev-parse --show-toplevel", stdout=str(tmp_path))
    workflow_path = _workflow_file(tmp_path)
    service = SymphonyService(workflow_path=workflow_path, runner=runner)
    tracker = _TrackerStub(_issue(), claimed=True)
    service.tracker = tracker  # type: ignore[assignment]

    outcome = ExecutionOutcome(issue_number=7, attempt=1, status="retry", reason="boom")
    service._schedule_or_fail(outcome)

    assert service.retry_entry is not None
    assert service.retry_entry.attempt == 2
    assert service.retry_entry.due_at > datetime.now(timezone.utc)


def test_service_marks_final_failure_when_retries_are_exhausted(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_command_result("git rev-parse --show-toplevel", stdout=str(tmp_path))
    workflow_path = _workflow_file(tmp_path)
    service = SymphonyService(workflow_path=workflow_path, runner=runner)
    tracker = _TrackerStub(_issue(), claimed=False)
    service.tracker = tracker  # type: ignore[assignment]

    outcome = ExecutionOutcome(issue_number=7, attempt=3, status="retry", reason="hard fail")
    service._schedule_or_fail(outcome)

    assert service.retry_entry is None
    assert tracker.failed
    assert tracker.released == [7]
    assert "hard fail" in tracker.failed[0][1]


class _AutoMergeTrackerStub:
    def __init__(self, pr_state: str = "success") -> None:
        self.merged: list[int] = []
        self.waited: list[int] = []
        self.pr_state = pr_state

    def wait_for_pr_checks(self, number: int, *, cwd: Path, timeout_seconds: int, poll_seconds: int):
        del cwd, timeout_seconds, poll_seconds
        self.waited.append(number)
        rollup = {
            "success": ({"status": "COMPLETED", "conclusion": "SUCCESS"},),
            "failed": ({"status": "COMPLETED", "conclusion": "FAILURE"},),
        }[self.pr_state]
        return type(
            "PR",
            (),
            {
                "number": number,
                "status_check_rollup": rollup,
                "review_decision": None,
                "merge_state_status": "CLEAN",
                "url": "https://example.test/pr/1",
            },
        )()

    def merge_pr(self, number: int, *, cwd: Path, merge_method: str, delete_branch: bool) -> None:
        del cwd, merge_method, delete_branch
        self.merged.append(number)

    def get_pr(self, number: int, *, cwd: Path):
        del cwd
        return type(
            "PR",
            (),
            {
                "number": number,
                "status_check_rollup": ({"status": "COMPLETED", "conclusion": "SUCCESS"},),
                "review_decision": None,
                "merge_state_status": "CLEAN",
                "url": "https://example.test/pr/1",
            },
        )()


def test_issue_executor_merges_after_passing_review_and_checks(tmp_path: Path) -> None:
    workflow = load_workflow(_automerge_workflow_file(tmp_path))
    tracker = _AutoMergeTrackerStub(pr_state="success")
    executor = IssueExecutor(
        workflow=workflow,
        worktrees=GitWorktreeManager(
            repo_root=tmp_path,
            workspace_root=tmp_path / "workspaces",
            base_branch="develop",
            runner=FakeRunner(),
        ),
        tracker=tracker,  # type: ignore[arg-type]
        runner=FakeRunner(),
    )
    review = _build_maintainer_review(
        changed_paths=("personaport/processor.py", "tests/test_processor.py"),
        validations=[],
        workflow=workflow,
        pr=type(
            "PR",
            (),
            {"number": 1, "is_draft": False, "status_check_rollup": (), "url": "https://example.test/pr/1"},
        )(),
    )

    pr, summary, merged = executor._review_and_maybe_merge(
        issue=_issue(),
        pr=type(
            "PR",
            (),
            {"number": 1, "is_draft": False, "status_check_rollup": (), "url": "https://example.test/pr/1"},
        )(),
        worktree=type("Worktree", (), {"path": tmp_path})(),
        maintainer_review=review,
        cancel_event=None,  # type: ignore[arg-type]
    )

    assert merged is True
    assert "merged" in summary.lower()
    assert tracker.waited == [1]
    assert tracker.merged == [1]
    assert pr.number == 1


def test_issue_executor_blocks_merge_for_high_risk_changes(tmp_path: Path) -> None:
    workflow = load_workflow(_automerge_workflow_file(tmp_path))
    tracker = _AutoMergeTrackerStub(pr_state="success")
    executor = IssueExecutor(
        workflow=workflow,
        worktrees=GitWorktreeManager(
            repo_root=tmp_path,
            workspace_root=tmp_path / "workspaces",
            base_branch="develop",
            runner=FakeRunner(),
        ),
        tracker=tracker,  # type: ignore[arg-type]
        runner=FakeRunner(),
    )
    pr_obj = type(
        "PR",
        (),
        {"number": 1, "is_draft": False, "status_check_rollup": (), "url": "https://example.test/pr/1"},
    )()
    review = _build_maintainer_review(
        changed_paths=("personaport/browser/platforms/chatgpt.py",),
        validations=[],
        workflow=workflow,
        pr=pr_obj,
    )

    pr, summary, merged = executor._review_and_maybe_merge(
        issue=_issue(),
        pr=pr_obj,
        worktree=type("Worktree", (), {"path": tmp_path})(),
        maintainer_review=review,
        cancel_event=None,  # type: ignore[arg-type]
    )

    assert merged is False
    assert "blocked" in summary.lower()
    assert tracker.waited == []
    assert tracker.merged == []
    assert pr.number == 1
