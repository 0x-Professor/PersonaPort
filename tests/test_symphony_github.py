from __future__ import annotations

from pathlib import Path

from tools.symphony.github import GitHubTracker, evaluate_pr_checks
from tools.symphony.models import LabelContract, TrackerConfig

from .symphony_test_helpers import FakeRunner


def test_github_tracker_filters_candidate_issues(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_json_result(
        "gh issue list",
        [
            {
                "number": 1,
                "title": "ready",
                "body": "",
                "url": "https://example.test/1",
                "state": "OPEN",
                "createdAt": "2026-03-01T00:00:00Z",
                "updatedAt": "2026-03-01T00:00:00Z",
                "labels": [{"name": "agent-ready"}],
            },
            {
                "number": 2,
                "title": "blocked",
                "body": "",
                "url": "https://example.test/2",
                "state": "OPEN",
                "createdAt": "2026-03-01T00:00:01Z",
                "updatedAt": "2026-03-01T00:00:01Z",
                "labels": [{"name": "blocked"}, {"name": "agent-ready"}],
            },
            {
                "number": 3,
                "title": "rework",
                "body": "",
                "url": "https://example.test/3",
                "state": "OPEN",
                "createdAt": "2026-03-01T00:00:02Z",
                "updatedAt": "2026-03-01T00:00:02Z",
                "labels": [{"name": "agent-rework"}],
            },
            {
                "number": 4,
                "title": "handoff",
                "body": "",
                "url": "https://example.test/4",
                "state": "OPEN",
                "createdAt": "2026-03-01T00:00:03Z",
                "updatedAt": "2026-03-01T00:00:03Z",
                "labels": [{"name": "human-review"}],
            },
        ],
    )
    tracker = GitHubTracker(
        config=TrackerConfig(kind="github", labels=LabelContract()),
        repo_root=tmp_path,
        runner=runner,
    )

    issues = tracker.list_candidate_issues()

    assert [issue.number for issue in issues] == [1, 3]


def test_github_tracker_claim_issue_edits_labels(tmp_path: Path) -> None:
    runner = FakeRunner()
    tracker = GitHubTracker(
        config=TrackerConfig(kind="github", labels=LabelContract()),
        repo_root=tmp_path,
        runner=runner,
    )

    tracker.claim_issue(42)

    assert any("--add-label \"agent-running\"" in call[0] for call in runner.calls)
    assert any("--remove-label \"agent-ready,agent-rework,human-review\"" in call[0] for call in runner.calls)


def test_evaluate_pr_checks_distinguishes_success_pending_and_failure() -> None:
    assert evaluate_pr_checks(({"status": "COMPLETED", "conclusion": "SUCCESS"},)) == "success"
    assert evaluate_pr_checks(({"status": "IN_PROGRESS", "conclusion": None},)) == "pending"
    assert evaluate_pr_checks(({"status": "COMPLETED", "conclusion": "FAILURE"},)) == "failed"
