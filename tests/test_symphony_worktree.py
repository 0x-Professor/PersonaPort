from __future__ import annotations

from pathlib import Path

from tools.symphony.models import GitHubIssue
from tools.symphony.worktree import GitWorktreeManager

from .symphony_test_helpers import FakeRunner


def _issue() -> GitHubIssue:
    return GitHubIssue(
        number=123,
        title="Fix login flow",
        body="",
        state="OPEN",
        url="https://example.test/issues/123",
        labels=frozenset({"agent-ready"}),
    )


def test_worktree_manager_creates_branch_from_origin_base(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspace_root = tmp_path / "workspaces"
    runner = FakeRunner()
    runner.add_command_result("refs/remotes/origin/develop", returncode=0)
    runner.add_command_result("refs/heads/agent/123-fix-login-flow", returncode=1)

    manager = GitWorktreeManager(
        repo_root=repo_root,
        workspace_root=workspace_root,
        base_branch="develop",
        runner=runner,
    )

    workspace = manager.ensure_workspace(_issue())

    assert workspace.created_now is True
    assert workspace.branch_name == "agent/123-fix-login-flow"
    worktree_add = next(
        raw_call
        for raw_call, _ in runner.raw_calls
        if isinstance(raw_call, list) and raw_call[:4] == ["git", "worktree", "add", "-b"]
    )
    assert worktree_add[4] == "agent/123-fix-login-flow"
    assert worktree_add[-1] == "origin/develop"


def test_worktree_manager_reuses_existing_workspace(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspace_root = tmp_path / "workspaces"
    path = workspace_root / "issue-123-fix-login-flow"
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir", encoding="utf-8")
    runner = FakeRunner()

    manager = GitWorktreeManager(
        repo_root=repo_root,
        workspace_root=workspace_root,
        base_branch="develop",
        runner=runner,
    )

    workspace = manager.ensure_workspace(_issue())

    assert workspace.created_now is False
    assert workspace.path == path.resolve()
    assert not any("git worktree add" in call[0] for call in runner.calls)


def test_worktree_manager_list_issue_workspaces_skips_non_git_directories(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspace_root = tmp_path / "workspaces"
    valid = workspace_root / "issue-123-fix-login-flow"
    valid.mkdir(parents=True)
    (valid / ".git").write_text("gitdir", encoding="utf-8")
    invalid = workspace_root / "issue-999-missing-git"
    invalid.mkdir()
    runner = FakeRunner()

    manager = GitWorktreeManager(
        repo_root=repo_root,
        workspace_root=workspace_root,
        base_branch="develop",
        runner=runner,
    )

    workspaces = manager.list_issue_workspaces()

    assert [workspace.issue_number for workspace in workspaces] == [123]
