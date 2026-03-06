from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LabelContract:
    ready: str = "agent-ready"
    running: str = "agent-running"
    handoff: str = "human-review"
    rework: str = "agent-rework"
    blocked: str = "blocked"

    def eligible_labels(self) -> tuple[str, str]:
        return (self.ready, self.rework)


@dataclass(frozen=True)
class TrackerConfig:
    kind: str = "github"
    repo: str | None = None
    labels: LabelContract = field(default_factory=LabelContract)


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: int = 30


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path
    base_branch: str = "develop"
    logs_root: Path | None = None


@dataclass(frozen=True)
class HookConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    timeout_seconds: int = 900


@dataclass(frozen=True)
class ValidationConfig:
    commands: tuple[str, ...] = ("ruff check .", "pytest")


@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = 1
    retry_backoff_seconds: int = 10
    max_retry_backoff_seconds: int = 300


@dataclass(frozen=True)
class PullRequestConfig:
    auto_merge: bool = False
    merge_method: str = "squash"
    delete_branch: bool = True
    checks_timeout_seconds: int = 1800
    checks_poll_seconds: int = 15
    high_risk_paths: tuple[str, ...] = (
        "personaport/browser/",
        "personaport/config.py",
        "personaport/db.py",
        "personaport/llm.py",
    )
    allowed_paths: tuple[str, ...] = (
        ".github/",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "README.md",
        "WORKFLOW.md",
        "docs/",
        "personaport/",
        "pyproject.toml",
        "requirements.txt",
        "tests/",
        "tools/",
    )


@dataclass(frozen=True)
class CodexConfig:
    command: str = "codex app-server"
    model: str | None = None
    model_provider: str | None = None
    approval_policy: str = "never"
    thread_sandbox: str = "workspace-write"
    turn_sandbox_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": [],
        }
    )
    turn_timeout_seconds: int = 1800
    read_timeout_seconds: int = 5


@dataclass(frozen=True)
class WorkflowConfig:
    workflow_path: Path
    prompt_template: str
    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HookConfig
    validation: ValidationConfig
    agent: AgentConfig
    pull_request: PullRequestConfig
    codex: CodexConfig
    raw_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    body: str
    state: str
    url: str | None
    labels: frozenset[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def normalized_state(self) -> str:
        return self.state.strip().lower()


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    is_draft: bool = False
    state: str = "OPEN"
    status_check_rollup: tuple[dict[str, Any], ...] = ()
    merge_state_status: str | None = None
    review_decision: str | None = None


@dataclass(frozen=True)
class WorktreeInfo:
    issue_number: int
    branch_name: str
    path: Path
    created_now: bool


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    assistant_text: str
    status: str
    error_message: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    stderr_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionOutcome:
    issue_number: int
    attempt: int
    status: str
    proof_markdown: str | None = None
    pr: PullRequestInfo | None = None
    reason: str | None = None
    branch_name: str | None = None
    workspace_path: Path | None = None


@dataclass(frozen=True)
class RetryEntry:
    issue: GitHubIssue
    attempt: int
    due_at: datetime
    reason: str
