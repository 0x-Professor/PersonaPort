from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .codex_app_server import CodexAppServerClient, CodexAppServerError
from .command import (
    CommandCancelledError,
    CommandExecutionError,
    CommandTimeoutError,
    ShellCommandRunner,
)
from .github import GitHubTracker, evaluate_pr_checks, summarize_status_rollup
from .models import (
    CommandResult,
    ExecutionOutcome,
    GitHubIssue,
    PullRequestInfo,
    RetryEntry,
    WorktreeInfo,
)
from .workflow import WorkflowLoadError, WorkflowManager, render_prompt
from .worktree import GitWorktreeManager


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_changed_files(result: CommandResult) -> str:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return "- No tracked file changes detected."
    return "\n".join(f"- `{line}`" for line in lines)


def _parse_changed_paths(result: CommandResult) -> tuple[str, ...]:
    paths: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        candidate = line[3:].strip() if len(line) > 3 else line
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1].strip()
        if candidate:
            paths.append(candidate.replace("\\", "/"))
    return tuple(paths)


def _format_validation(results: list[CommandResult]) -> str:
    lines: list[str] = []
    for result in results:
        state = "passed" if result.passed else "failed"
        lines.append(f"- `{result.command}`: {state}")
    return "\n".join(lines) or "- No validation commands were configured."


def _build_pr_title(issue: GitHubIssue) -> str:
    return f"[Agent] {issue.title} (#{issue.number})"


def _build_pr_body(issue: GitHubIssue, attempt: int, proof_summary: str) -> str:
    return (
        f"Closes #{issue.number}\n\n"
        f"Automated run attempt: {attempt}\n\n"
        f"{proof_summary}\n"
    )


def _build_failure_proof(issue: GitHubIssue, reason: str, attempt: int) -> str:
    return (
        f"## Symphony Run Failed\n\n"
        f"- Issue: #{issue.number} {issue.title}\n"
        f"- Attempt: {attempt}\n"
        f"- Failure: {reason}\n"
        f"- Next step: relabel with `agent-rework` or `agent-ready` after fixing the blocker.\n"
    )


def _build_handoff_proof(
    *,
    issue: GitHubIssue,
    attempt: int,
    pr: PullRequestInfo | None,
    changed_files: CommandResult,
    validations: list[CommandResult],
    maintainer_review: "MaintainerReview",
    codex_summary: str,
    merge_summary: str,
) -> str:
    pr_line = f"- PR: {pr.url}" if pr is not None else "- PR: not created"
    ci_summary = summarize_status_rollup(pr.status_check_rollup) if pr is not None else "Checks unavailable."
    return (
        f"## Symphony Proof of Work\n\n"
        f"- Issue: #{issue.number} {issue.title}\n"
        f"- Attempt: {attempt}\n"
        f"{pr_line}\n\n"
        f"### Changed Files\n{_format_changed_files(changed_files)}\n\n"
        f"### Validation\n{_format_validation(validations)}\n\n"
        f"### Maintainer Review\n{_format_maintainer_review(maintainer_review)}\n\n"
        f"### CI Status\n{ci_summary}\n\n"
        f"### Merge Status\n{merge_summary}\n\n"
        f"### Codex Summary\n{codex_summary or 'No assistant summary captured.'}\n"
    )


@dataclass
class ActiveRun:
    issue: GitHubIssue
    attempt: int
    cancel_event: threading.Event
    thread: threading.Thread
    result_holder: dict[str, object]


@dataclass(frozen=True)
class MaintainerReview:
    approved_for_merge: bool
    changed_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def _format_maintainer_review(review: MaintainerReview) -> str:
    lines = [
        f"- Auto-merge decision: {'approved' if review.approved_for_merge else 'handoff required'}",
        (
            "- Changed paths: "
            + (", ".join(f"`{path}`" for path in review.changed_paths) if review.changed_paths else "none")
        ),
    ]
    if review.high_risk_paths:
        lines.append(
            "- High-risk paths: "
            + ", ".join(f"`{path}`" for path in review.high_risk_paths)
        )
    if review.blockers:
        lines.extend(f"- Blocker: {blocker}" for blocker in review.blockers)
    if review.warnings:
        lines.extend(f"- Warning: {warning}" for warning in review.warnings)
    return "\n".join(lines)


def _path_matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in prefixes
    )


def _build_maintainer_review(
    *,
    changed_paths: tuple[str, ...],
    validations: list[CommandResult],
    workflow,
    pr: PullRequestInfo | None,
) -> MaintainerReview:
    blockers: list[str] = []
    warnings: list[str] = []
    high_risk_paths = tuple(
        path
        for path in changed_paths
        if _path_matches_prefix(path, workflow.pull_request.high_risk_paths)
    )
    unknown_paths = tuple(
        path
        for path in changed_paths
        if not _path_matches_prefix(path, workflow.pull_request.allowed_paths)
    )
    failed_validations = tuple(result.command for result in validations if not result.passed)

    if not changed_paths:
        blockers.append("No project changes were produced, so there is nothing to merge.")
    if failed_validations:
        blockers.append(
            "Local validation failed: " + ", ".join(f"`{command}`" for command in failed_validations)
        )
    if high_risk_paths:
        blockers.append(
            "High-risk auth/browser/secret handling files changed and require human review."
        )
    if unknown_paths:
        blockers.append(
            "Changed files fall outside the declared repo scope: "
            + ", ".join(f"`{path}`" for path in unknown_paths)
        )
    if pr is None:
        warnings.append("No PR was created because the worktree had no tracked changes.")
    elif pr.is_draft:
        warnings.append("The PR is draft because local validation did not fully pass.")
    if any("personaport/browser/" in path or "playwright" in path for path in changed_paths):
        warnings.append("Browser behavior changed; screenshots/video were not captured automatically.")

    return MaintainerReview(
        approved_for_merge=not blockers and pr is not None and not pr.is_draft,
        changed_paths=changed_paths,
        high_risk_paths=high_risk_paths,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


class IssueExecutor:
    def __init__(
        self,
        *,
        workflow,
        worktrees: GitWorktreeManager,
        tracker: GitHubTracker,
        runner: ShellCommandRunner | None = None,
    ) -> None:
        self.workflow = workflow
        self.worktrees = worktrees
        self.tracker = tracker
        self.runner = runner or ShellCommandRunner()

    def execute(
        self,
        issue: GitHubIssue,
        *,
        attempt: int,
        cancel_event: threading.Event,
    ) -> ExecutionOutcome:
        worktree = self.worktrees.ensure_workspace(issue)
        try:
            self._run_hook(
                self.workflow.hooks.after_create,
                worktree,
                cancel_event,
                created_only=worktree.created_now,
            )
            self._run_hook(
                self.workflow.hooks.before_run,
                worktree,
                cancel_event,
                created_only=False,
            )
            prompt = render_prompt(self.workflow, issue, attempt=attempt)
            codex = CodexAppServerClient(self.workflow.codex)
            codex_result = codex.run_prompt(
                prompt=prompt,
                workspace=worktree.path,
                cancel_event=cancel_event,
            )
            changed_files = self.runner.run(
                ["git", "status", "--short"],
                cwd=worktree.path,
                cancel_event=cancel_event,
                check=False,
            )
            changed_paths = _parse_changed_paths(changed_files)
            validations = self._run_validation(worktree.path, cancel_event)
            pr = self._commit_push_and_open_pr(
                issue=issue,
                attempt=attempt,
                worktree=worktree,
                changed_files=changed_files,
                validations=validations,
                cancel_event=cancel_event,
            )
            maintainer_review = _build_maintainer_review(
                changed_paths=changed_paths,
                validations=validations,
                workflow=self.workflow,
                pr=pr,
            )
            pr, merge_summary, merged = self._review_and_maybe_merge(
                issue=issue,
                pr=pr,
                worktree=worktree,
                maintainer_review=maintainer_review,
                cancel_event=cancel_event,
            )
            proof = _build_handoff_proof(
                issue=issue,
                attempt=attempt,
                pr=pr,
                changed_files=changed_files,
                validations=validations,
                maintainer_review=maintainer_review,
                codex_summary=codex_result.assistant_text,
                merge_summary=merge_summary,
            )
            return ExecutionOutcome(
                issue_number=issue.number,
                attempt=attempt,
                status="merged" if merged else "handoff",
                proof_markdown=proof,
                pr=pr,
                branch_name=worktree.branch_name,
                workspace_path=worktree.path,
            )
        except (
            CommandCancelledError,
            CodexAppServerError,
            CommandExecutionError,
            CommandTimeoutError,
            WorkflowLoadError,
            RuntimeError,
        ) as exc:
            return ExecutionOutcome(
                issue_number=issue.number,
                attempt=attempt,
                status="retry",
                reason=str(exc),
                branch_name=worktree.branch_name,
                workspace_path=worktree.path,
            )
        finally:
            try:
                self._run_hook(
                    self.workflow.hooks.after_run,
                    worktree,
                    cancel_event,
                    created_only=False,
                )
            except RuntimeError:
                pass

    def _run_hook(
        self,
        script: str | None,
        worktree: WorktreeInfo,
        cancel_event: threading.Event,
        *,
        created_only: bool,
    ) -> None:
        if not script:
            return
        if created_only and not worktree.created_now:
            return
        self.runner.run(
            script,
            cwd=worktree.path,
            timeout_seconds=self.workflow.hooks.timeout_seconds,
            cancel_event=cancel_event,
            shell=True,
        )

    def _run_validation(
        self,
        workspace: Path,
        cancel_event: threading.Event,
    ) -> list[CommandResult]:
        results: list[CommandResult] = []
        for command in self.workflow.validation.commands:
            results.append(
                self.runner.run(
                    command,
                    cwd=workspace,
                    cancel_event=cancel_event,
                    check=False,
                    shell=True,
                )
            )
        return results

    def _commit_push_and_open_pr(
        self,
        *,
        issue: GitHubIssue,
        attempt: int,
        worktree: WorktreeInfo,
        changed_files: CommandResult,
        validations: list[CommandResult],
        cancel_event: threading.Event,
    ) -> PullRequestInfo | None:
        if not changed_files.stdout.strip():
            return None
        self.runner.run(["git", "add", "-A"], cwd=worktree.path, cancel_event=cancel_event)
        commit_message = f"agent: resolve #{issue.number} {issue.title[:50]}"
        commit_result = self.runner.run(
            ["git", "commit", "-m", commit_message],
            cwd=worktree.path,
            cancel_event=cancel_event,
            check=False,
        )
        combined_output = f"{commit_result.stdout}\n{commit_result.stderr}".lower()
        if commit_result.returncode != 0 and "nothing to commit" not in combined_output:
            raise CommandExecutionError(commit_result)
        self.runner.run(
            ["git", "push", "-u", "origin", worktree.branch_name],
            cwd=worktree.path,
            cancel_event=cancel_event,
        )
        proof_summary = "Local validation results:\n" + _format_validation(validations)
        draft = any(not result.passed for result in validations)
        return self.tracker.ensure_pr(
            branch_name=worktree.branch_name,
            base_branch=self.workflow.workspace.base_branch,
            title=_build_pr_title(issue),
            body=_build_pr_body(issue, attempt, proof_summary),
            draft=draft,
            cwd=worktree.path,
        )

    def _review_and_maybe_merge(
        self,
        *,
        issue: GitHubIssue,
        pr: PullRequestInfo | None,
        worktree: WorktreeInfo,
        maintainer_review: MaintainerReview,
        cancel_event: threading.Event,
    ) -> tuple[PullRequestInfo | None, str, bool]:
        del issue, cancel_event
        if pr is None:
            return None, "No PR created because there were no tracked code changes.", False
        if not self.workflow.pull_request.auto_merge:
            return pr, "Auto-merge is disabled in the workflow contract.", False
        if not maintainer_review.approved_for_merge:
            return pr, "Maintainer review blocked auto-merge.", False

        refreshed_pr = self.tracker.wait_for_pr_checks(
            pr.number,
            cwd=worktree.path,
            timeout_seconds=self.workflow.pull_request.checks_timeout_seconds,
            poll_seconds=self.workflow.pull_request.checks_poll_seconds,
        )
        check_state = evaluate_pr_checks(refreshed_pr.status_check_rollup)
        if check_state != "success":
            return refreshed_pr, f"GitHub checks did not finish successfully: {check_state}.", False
        if refreshed_pr.review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
            return (
                refreshed_pr,
                f"GitHub review decision requires human action: {refreshed_pr.review_decision}.",
                False,
            )
        if refreshed_pr.merge_state_status in {"BLOCKED", "DIRTY", "BEHIND", "DRAFT", "UNSTABLE"}:
            return (
                refreshed_pr,
                f"GitHub merge state is not mergeable yet: {refreshed_pr.merge_state_status}.",
                False,
            )
        self.tracker.merge_pr(
            refreshed_pr.number,
            cwd=worktree.path,
            merge_method=self.workflow.pull_request.merge_method,
            delete_branch=self.workflow.pull_request.delete_branch,
        )
        final_pr = self.tracker.get_pr(refreshed_pr.number, cwd=worktree.path)
        return final_pr, "PR was merged after maintainer review and passing checks.", True


class StructuredLogger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: object) -> None:
        payload = {"timestamp": _utc_now().isoformat(), "event": event, **fields}
        line = json.dumps(payload, sort_keys=True)
        print(line)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class SymphonyService:
    def __init__(
        self,
        *,
        workflow_path: Path,
        runner: ShellCommandRunner | None = None,
    ) -> None:
        self.runner = runner or ShellCommandRunner()
        repo_root_text = self.runner.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=workflow_path.parent,
        ).stdout.strip()
        self.repo_root = Path(repo_root_text).resolve()
        self.workflow_manager = WorkflowManager(workflow_path)
        self.workflow = self.workflow_manager.current
        self.logger = StructuredLogger(
            (self.workflow.workspace.logs_root or (self.repo_root / ".symphony" / "logs"))
            / "symphony.log"
        )
        self.tracker = GitHubTracker(
            config=self.workflow.tracker,
            repo_root=self.repo_root,
            runner=self.runner,
        )
        self.worktrees = GitWorktreeManager(
            repo_root=self.repo_root,
            workspace_root=self.workflow.workspace.root,
            base_branch=self.workflow.workspace.base_branch,
            runner=self.runner,
        )
        self.executor = IssueExecutor(
            workflow=self.workflow,
            worktrees=self.worktrees,
            tracker=self.tracker,
            runner=self.runner,
        )
        self.retry_entry: RetryEntry | None = None
        self.active_run: ActiveRun | None = None

    def serve(self, *, once: bool = False) -> None:
        self._preflight()
        self.tracker.requeue_stale_running_issues()
        while True:
            self._reload_workflow_if_needed()
            self._cleanup_closed_workspaces()
            self._tick()
            if once and self.active_run is None and self.retry_entry is None:
                return
            sleep_seconds = (
                1
                if self.active_run is not None or self.retry_entry is not None
                else self.workflow.polling.interval_seconds
            )
            time.sleep(sleep_seconds)

    def _preflight(self) -> None:
        self.runner.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.repo_root)
        self.runner.run(["codex", "--version"], cwd=self.repo_root)
        self.tracker.validate_auth()

    def _reload_workflow_if_needed(self) -> None:
        updated = self.workflow_manager.reload_if_changed()
        if updated is None:
            return
        self.workflow = updated
        self.worktrees = GitWorktreeManager(
            repo_root=self.repo_root,
            workspace_root=self.workflow.workspace.root,
            base_branch=self.workflow.workspace.base_branch,
            runner=self.runner,
        )
        self.tracker = GitHubTracker(
            config=self.workflow.tracker,
            repo_root=self.repo_root,
            runner=self.runner,
        )
        self.executor = IssueExecutor(
            workflow=self.workflow,
            worktrees=self.worktrees,
            tracker=self.tracker,
            runner=self.runner,
        )
        self.logger.log("workflow_reloaded", path=str(self.workflow.workflow_path))

    def _cleanup_closed_workspaces(self) -> None:
        for workspace in self.worktrees.list_issue_workspaces():
            issue = self.tracker.get_issue(workspace.issue_number)
            if issue.normalized_state != "open":
                self.logger.log("cleanup_workspace", issue_number=workspace.issue_number)
                self.worktrees.cleanup_workspace(workspace.issue_number)

    def _tick(self) -> None:
        if self.active_run is not None:
            self._poll_active_run()
            return
        if self.retry_entry is not None:
            if _utc_now() >= self.retry_entry.due_at:
                issue = self.tracker.get_issue(self.retry_entry.issue.number)
                if self.tracker.is_issue_still_claimed(issue):
                    self._start_issue(issue, attempt=self.retry_entry.attempt)
                else:
                    self.logger.log("retry_aborted", issue_number=issue.number)
                self.retry_entry = None
            return
        issues = self.tracker.list_candidate_issues()
        if not issues:
            self.logger.log("idle")
            return
        self._start_issue(issues[0], attempt=1)

    def _start_issue(self, issue: GitHubIssue, *, attempt: int) -> None:
        self.tracker.claim_issue(issue.number)
        cancel_event = threading.Event()
        result_holder: dict[str, object] = {}
        thread = threading.Thread(
            target=self._execute_issue_thread,
            args=(issue, attempt, cancel_event, result_holder),
            daemon=True,
        )
        thread.start()
        self.active_run = ActiveRun(
            issue=issue,
            attempt=attempt,
            cancel_event=cancel_event,
            thread=thread,
            result_holder=result_holder,
        )
        self.logger.log("issue_started", issue_number=issue.number, attempt=attempt)

    def _execute_issue_thread(
        self,
        issue: GitHubIssue,
        attempt: int,
        cancel_event: threading.Event,
        result_holder: dict[str, object],
    ) -> None:
        result_holder["outcome"] = self.executor.execute(
            issue,
            attempt=attempt,
            cancel_event=cancel_event,
        )

    def _poll_active_run(self) -> None:
        assert self.active_run is not None
        current_issue = self.tracker.get_issue(self.active_run.issue.number)
        if not self.tracker.is_issue_still_claimed(current_issue):
            self.active_run.cancel_event.set()
        if self.active_run.thread.is_alive():
            return
        outcome = self.active_run.result_holder.get("outcome")
        self.active_run = None
        if not isinstance(outcome, ExecutionOutcome):
            return
        if outcome.status == "merged":
            if outcome.proof_markdown:
                self.tracker.comment_on_issue(outcome.issue_number, outcome.proof_markdown)
            try:
                current_issue = self.tracker.get_issue(outcome.issue_number)
                if current_issue.normalized_state == "open":
                    self.tracker.release_issue(outcome.issue_number)
            except Exception as exc:
                self.logger.log(
                    "issue_release_failed",
                    issue_number=outcome.issue_number,
                    error=str(exc),
                )
            self.worktrees.cleanup_workspace(outcome.issue_number)
            self.logger.log("issue_merged", issue_number=outcome.issue_number)
            return
        if outcome.status == "handoff":
            self.tracker.handoff_issue(
                outcome.issue_number,
                proof_markdown=outcome.proof_markdown or "",
            )
            self.logger.log("issue_handoff", issue_number=outcome.issue_number)
            return
        self._schedule_or_fail(outcome)

    def _schedule_or_fail(self, outcome: ExecutionOutcome) -> None:
        issue = self.tracker.get_issue(outcome.issue_number)
        current_attempt = outcome.attempt
        next_attempt = current_attempt + 1
        max_backoff = self.workflow.agent.max_retry_backoff_seconds
        delay_seconds = min(
            self.workflow.agent.retry_backoff_seconds * (2 ** max(0, next_attempt - 2)),
            max_backoff,
        )
        if next_attempt <= 3 and self.tracker.is_issue_still_claimed(issue):
            self.retry_entry = RetryEntry(
                issue=issue,
                attempt=next_attempt,
                due_at=_utc_now() + timedelta(seconds=delay_seconds),
                reason=outcome.reason or "retry requested",
            )
            self.logger.log(
                "issue_retry_scheduled",
                issue_number=issue.number,
                attempt=next_attempt,
                delay_seconds=delay_seconds,
                reason=outcome.reason,
            )
            return
        proof = _build_failure_proof(
            issue,
            outcome.reason or "Unknown failure",
            next_attempt - 1,
        )
        self.tracker.fail_issue(issue.number, proof_markdown=proof)
        self.tracker.release_issue(issue.number)
        self.logger.log(
            "issue_failed",
            issue_number=issue.number,
            reason=outcome.reason,
        )
