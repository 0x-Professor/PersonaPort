from __future__ import annotations

from pathlib import Path

from tools.symphony.github import GitHubTracker
from tools.symphony.models import LabelContract, TrackerConfig

from .symphony_test_helpers import FakeRunner


def test_ensure_pr_passes_title_as_a_single_argument(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_json_result("gh pr list", [])
    runner.add_command_result("gh pr create", stdout="https://example.test/pr/3\n")
    runner.add_json_result(
        "gh pr list",
        [
            {
                "number": 3,
                "url": "https://example.test/pr/3",
                "isDraft": True,
                "state": "OPEN",
                "statusCheckRollup": [],
                "mergeStateStatus": "CLEAN",
                "reviewDecision": None,
            }
        ],
    )
    tracker = GitHubTracker(
        config=TrackerConfig(repo="owner/repo", labels=LabelContract()),
        repo_root=tmp_path,
        runner=runner,
    )

    pr = tracker.ensure_pr(
        branch_name="agent/7-fix-quotes",
        base_branch="master",
        title='Fix "quotes" && echo nope',
        body="Body",
        draft=True,
        cwd=tmp_path,
    )

    assert pr.number == 3
    create_call = next(
        raw_call
        for raw_call, _ in runner.raw_calls
        if isinstance(raw_call, list) and raw_call[:3] == ["gh", "pr", "create"]
    )
    assert create_call[create_call.index("--title") + 1] == 'Fix "quotes" && echo nope'
    assert create_call[create_call.index("--base") + 1] == "master"
    assert "--draft" in create_call


def test_fail_issue_delegates_to_handoff_issue(tmp_path: Path) -> None:
    tracker = GitHubTracker(
        config=TrackerConfig(repo="owner/repo", labels=LabelContract()),
        repo_root=tmp_path,
        runner=FakeRunner(),
    )
    calls: list[tuple[int, str]] = []

    def _fake_handoff(number: int, *, proof_markdown: str) -> None:
        calls.append((number, proof_markdown))

    tracker.handoff_issue = _fake_handoff  # type: ignore[method-assign]

    tracker.fail_issue(7, proof_markdown="body")

    assert calls == [(7, "body")]
