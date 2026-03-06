from __future__ import annotations

import re
from pathlib import Path

from .command import ShellCommandRunner
from .models import GitHubIssue, WorktreeInfo


def slugify_issue_title(title: str, *, max_length: int = 48) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    if not cleaned:
        cleaned = "issue"
    return cleaned[:max_length].rstrip("-")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class GitWorktreeManager:
    def __init__(
        self,
        *,
        repo_root: Path,
        workspace_root: Path,
        base_branch: str,
        runner: ShellCommandRunner | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.base_branch = base_branch
        self.runner = runner or ShellCommandRunner()

    def ensure_workspace(self, issue: GitHubIssue) -> WorktreeInfo:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        slug = slugify_issue_title(issue.title)
        branch_name = f"agent/{issue.number}-{slug}"
        path = (self.workspace_root / f"issue-{issue.number}-{slug}").resolve()
        if not _is_relative_to(path, self.workspace_root):
            raise RuntimeError(f"Resolved workspace escaped root: {path}")
        if path.exists():
            if not (path / ".git").exists():
                raise RuntimeError(f"Existing path is not a git worktree: {path}")
            return WorktreeInfo(
                issue_number=issue.number,
                branch_name=branch_name,
                path=path,
                created_now=False,
            )
        base_ref = self._resolve_base_ref()
        if self._branch_exists(branch_name):
            command = f'git worktree add "{path}" "{branch_name}"'
        else:
            command = f'git worktree add -b "{branch_name}" "{path}" "{base_ref}"'
        self.runner.run(command, cwd=self.repo_root)
        return WorktreeInfo(
            issue_number=issue.number,
            branch_name=branch_name,
            path=path,
            created_now=True,
        )

    def cleanup_workspace(self, issue_number: int) -> None:
        for workspace in self.list_issue_workspaces():
            if workspace.issue_number != issue_number:
                continue
            self.runner.run(f'git worktree remove --force "{workspace.path}"', cwd=self.repo_root)

    def list_issue_workspaces(self) -> list[WorktreeInfo]:
        if not self.workspace_root.exists():
            return []
        items: list[WorktreeInfo] = []
        for child in self.workspace_root.iterdir():
            if not child.is_dir():
                continue
            match = re.match(r"issue-(\d+)-", child.name)
            if not match:
                continue
            items.append(
                WorktreeInfo(
                    issue_number=int(match.group(1)),
                    branch_name="",
                    path=child.resolve(),
                    created_now=False,
                )
            )
        return sorted(items, key=lambda item: item.issue_number)

    def _resolve_base_ref(self) -> str:
        remote_ref = f"origin/{self.base_branch}"
        if self._ref_exists(f"refs/remotes/{remote_ref}"):
            return remote_ref
        if self._ref_exists(f"refs/heads/{self.base_branch}"):
            return self.base_branch
        raise RuntimeError(
            f"Base branch `{self.base_branch}` was not found locally or under origin/."
        )

    def _branch_exists(self, branch_name: str) -> bool:
        return self._ref_exists(f"refs/heads/{branch_name}")

    def _ref_exists(self, ref_name: str) -> bool:
        result = self.runner.run(
            f'git rev-parse --verify --quiet "{ref_name}"',
            cwd=self.repo_root,
            check=False,
        )
        return result.returncode == 0
