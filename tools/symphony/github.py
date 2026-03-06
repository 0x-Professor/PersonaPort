from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .command import ShellCommandRunner
from .models import GitHubIssue, PullRequestInfo, TrackerConfig


def _parse_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


class GitHubTracker:
    def __init__(
        self,
        *,
        config: TrackerConfig,
        repo_root: Path,
        runner: ShellCommandRunner | None = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root
        self.runner = runner or ShellCommandRunner()

    def validate_auth(self) -> None:
        self.runner.run(["gh", "auth", "status"], cwd=self.repo_root)

    def _repo_args(self) -> list[str]:
        if not self.config.repo:
            return []
        return ["-R", self.config.repo]

    def list_open_issues(self) -> list[GitHubIssue]:
        command = [
            "gh",
            "issue",
            "list",
            *self._repo_args(),
            "--limit",
            "200",
            "--state",
            "open",
            "--json",
            "number,title,body,url,labels,createdAt,updatedAt,state",
        ]
        payload = self.runner.run_json(command, cwd=self.repo_root)
        assert isinstance(payload, list)
        issues = [self._parse_issue(item) for item in payload]
        issues.sort(
            key=lambda item: item.created_at or datetime.fromtimestamp(0, tz=timezone.utc)
        )
        return issues

    def list_candidate_issues(self) -> list[GitHubIssue]:
        return [issue for issue in self.list_open_issues() if self.is_issue_eligible(issue)]

    def list_running_issues(self) -> list[GitHubIssue]:
        running_label = self.config.labels.running.lower()
        return [issue for issue in self.list_open_issues() if running_label in issue.labels]

    def get_issue(self, number: int) -> GitHubIssue:
        command = [
            "gh",
            "issue",
            "view",
            str(number),
            *self._repo_args(),
            "--json",
            "number,title,body,url,labels,createdAt,updatedAt,state",
        ]
        payload = self.runner.run_json(command, cwd=self.repo_root)
        assert isinstance(payload, dict)
        return self._parse_issue(payload)

    def claim_issue(self, number: int) -> None:
        labels = self.config.labels
        self._edit_issue_labels(
            number,
            add=(labels.running,),
            remove=(labels.ready, labels.rework, labels.handoff),
        )

    def handoff_issue(self, number: int, *, proof_markdown: str) -> None:
        labels = self.config.labels
        self.comment_on_issue(number, proof_markdown)
        self._edit_issue_labels(
            number,
            add=(labels.handoff,),
            remove=(labels.running, labels.ready, labels.rework),
        )

    def fail_issue(self, number: int, *, proof_markdown: str) -> None:
        self.handoff_issue(number, proof_markdown=proof_markdown)

    def release_issue(self, number: int) -> None:
        self._edit_issue_labels(number, add=(), remove=(self.config.labels.running,))

    def requeue_stale_running_issues(self) -> None:
        labels = self.config.labels
        for issue in self.list_running_issues():
            self._edit_issue_labels(
                issue.number,
                add=(labels.ready,),
                remove=(labels.running,),
            )

    def ensure_pr(
        self,
        *,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool,
        cwd: Path,
    ) -> PullRequestInfo:
        existing = self.find_pr_by_branch(branch_name, cwd=cwd)
        if existing is not None:
            return existing
        command = [
            "gh",
            "pr",
            "create",
            *self._repo_args(),
            "--base",
            base_branch,
            "--head",
            branch_name,
            "--title",
            title,
            "--body-file",
            "-",
        ]
        if draft:
            command.append("--draft")
        result = self.runner.run(command, cwd=cwd, input_text=body)
        pr_url = result.stdout.strip().splitlines()[-1]
        return self.find_pr_by_branch(branch_name, cwd=cwd) or PullRequestInfo(
            number=0,
            url=pr_url,
            is_draft=draft,
        )

    def find_pr_by_branch(self, branch_name: str, *, cwd: Path) -> PullRequestInfo | None:
        command = [
            "gh",
            "pr",
            "list",
            *self._repo_args(),
            "--head",
            branch_name,
            "--state",
            "all",
            "--json",
            "number,url,isDraft,state,statusCheckRollup,mergeStateStatus,reviewDecision",
        ]
        payload = self.runner.run_json(command, cwd=cwd)
        assert isinstance(payload, list)
        if not payload:
            return None
        return self._parse_pr(payload[0])

    def get_pr(self, number: int, *, cwd: Path) -> PullRequestInfo:
        command = [
            "gh",
            "pr",
            "view",
            str(number),
            *self._repo_args(),
            "--json",
            "number,url,isDraft,state,statusCheckRollup,mergeStateStatus,reviewDecision",
        ]
        payload = self.runner.run_json(command, cwd=cwd)
        assert isinstance(payload, dict)
        return self._parse_pr(payload)

    def wait_for_pr_checks(
        self,
        number: int,
        *,
        cwd: Path,
        timeout_seconds: int,
        poll_seconds: int,
    ) -> PullRequestInfo:
        deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
        latest = self.get_pr(number, cwd=cwd)
        while datetime.now(timezone.utc).timestamp() <= deadline:
            latest = self.get_pr(number, cwd=cwd)
            state = evaluate_pr_checks(latest.status_check_rollup)
            if state in {"success", "failed"}:
                return latest
            time.sleep(poll_seconds)
        return latest

    def merge_pr(
        self,
        number: int,
        *,
        cwd: Path,
        merge_method: str,
        delete_branch: bool,
    ) -> None:
        method_flag = {
            "merge": "--merge",
            "squash": "--squash",
            "rebase": "--rebase",
        }[merge_method]
        command = ["gh", "pr", "merge", str(number), *self._repo_args(), method_flag]
        if delete_branch:
            command.append("--delete-branch")
        self.runner.run(command, cwd=cwd)

    def comment_on_issue(self, number: int, body: str) -> None:
        command = [
            "gh",
            "issue",
            "comment",
            str(number),
            *self._repo_args(),
            "--body-file",
            "-",
        ]
        self.runner.run(command, cwd=self.repo_root, input_text=body)

    def is_issue_eligible(self, issue: GitHubIssue) -> bool:
        labels = self.config.labels
        if issue.normalized_state != "open":
            return False
        if labels.blocked.lower() in issue.labels:
            return False
        if labels.running.lower() in issue.labels:
            return False
        if labels.handoff.lower() in issue.labels:
            return False
        return any(label.lower() in issue.labels for label in labels.eligible_labels())

    def is_issue_still_claimed(self, issue: GitHubIssue) -> bool:
        if issue.normalized_state != "open":
            return False
        if self.config.labels.blocked.lower() in issue.labels:
            return False
        return self.config.labels.running.lower() in issue.labels

    def _edit_issue_labels(
        self,
        number: int,
        *,
        add: tuple[str, ...],
        remove: tuple[str, ...],
    ) -> None:
        command = ["gh", "issue", "edit", str(number), *self._repo_args()]
        if add:
            command.extend(["--add-label", ",".join(add)])
        if remove:
            command.extend(["--remove-label", ",".join(remove)])
        self.runner.run(command, cwd=self.repo_root)

    def _parse_issue(self, payload: dict[str, Any]) -> GitHubIssue:
        labels_raw = payload.get("labels") or []
        labels = frozenset(
            str(item.get("name", "")).strip().lower()
            for item in labels_raw
            if isinstance(item, dict) and item.get("name")
        )
        return GitHubIssue(
            number=int(payload.get("number", 0)),
            title=str(payload.get("title", "")).strip(),
            body=str(payload.get("body", "") or ""),
            state=str(payload.get("state", "OPEN")),
            url=(str(payload["url"]) if payload.get("url") else None),
            labels=labels,
            created_at=_parse_datetime(payload.get("createdAt")),
            updated_at=_parse_datetime(payload.get("updatedAt")),
        )

    def _parse_pr(self, entry: dict[str, Any]) -> PullRequestInfo:
        return PullRequestInfo(
            number=int(entry.get("number", 0)),
            url=str(entry.get("url")),
            is_draft=bool(entry.get("isDraft", False)),
            state=str(entry.get("state", "OPEN")),
            status_check_rollup=tuple(entry.get("statusCheckRollup") or ()),
            merge_state_status=(
                str(entry["mergeStateStatus"]) if entry.get("mergeStateStatus") else None
            ),
            review_decision=(
                str(entry["reviewDecision"]) if entry.get("reviewDecision") else None
            ),
        )


def summarize_status_rollup(rollup: tuple[dict[str, Any], ...]) -> str:
    if not rollup:
        return "Checks not available yet."
    lines: list[str] = []
    for item in rollup:
        name = item.get("name") or item.get("context") or item.get("workflowName") or "check"
        state = item.get("conclusion") or item.get("status") or "unknown"
        url = item.get("detailsUrl")
        if url:
            lines.append(f"- {name}: {state} ({url})")
        else:
            lines.append(f"- {name}: {state}")
    return "\n".join(lines)


def evaluate_pr_checks(rollup: tuple[dict[str, Any], ...]) -> str:
    if not rollup:
        return "pending"
    has_pending = False
    for item in rollup:
        conclusion = str(item.get("conclusion") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if conclusion in {"success", "neutral", "skipped"}:
            continue
        if conclusion in {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}:
            return "failed"
        if status in {"completed"} and not conclusion:
            continue
        if status in {"queued", "in_progress", "pending", "requested", "waiting"}:
            has_pending = True
            continue
        if conclusion:
            return "failed"
        has_pending = True
    return "pending" if has_pending else "success"
