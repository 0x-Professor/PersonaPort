from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError

from .models import (
    AgentConfig,
    CodexConfig,
    GitHubIssue,
    HookConfig,
    LabelContract,
    PollingConfig,
    PullRequestConfig,
    TrackerConfig,
    ValidationConfig,
    WorkflowConfig,
    WorkspaceConfig,
)


class WorkflowLoadError(RuntimeError):
    """Raised when WORKFLOW.md cannot be parsed or validated."""


def _expand_path(raw_value: str | None, *, base_dir: Path) -> Path | None:
    if raw_value is None:
        return None
    expanded = os.path.expandvars(raw_value)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _coerce_positive_int(value: Any, *, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowLoadError(f"{field_name} must be an integer.") from exc
    if parsed <= 0:
        raise WorkflowLoadError(f"{field_name} must be positive.")
    return parsed


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            yaml_block = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :]).strip()
            try:
                parsed = yaml.safe_load(yaml_block) or {}
            except yaml.YAMLError as exc:
                raise WorkflowLoadError("Invalid YAML front matter in WORKFLOW.md.") from exc
            if not isinstance(parsed, dict):
                raise WorkflowLoadError("WORKFLOW.md front matter must decode to a mapping.")
            return parsed, body
    raise WorkflowLoadError("WORKFLOW.md front matter is missing a closing --- delimiter.")


def load_workflow(path: Path | str) -> WorkflowConfig:
    workflow_path = Path(path).expanduser().resolve()
    if not workflow_path.exists():
        raise WorkflowLoadError(f"Workflow file not found: {workflow_path}")
    raw_text = workflow_path.read_text(encoding="utf-8")
    config_map, prompt_body = _split_front_matter(raw_text)

    tracker_map = dict(config_map.get("tracker") or {})
    tracker_kind = str(tracker_map.get("kind", "github")).strip().lower()
    if tracker_kind != "github":
        raise WorkflowLoadError("tracker.kind must be `github` for this repository runner.")
    label_map = dict(tracker_map.get("labels") or {})
    labels = LabelContract(
        ready=str(label_map.get("ready", "agent-ready")).strip(),
        running=str(label_map.get("running", "agent-running")).strip(),
        handoff=str(label_map.get("handoff", "human-review")).strip(),
        rework=str(label_map.get("rework", "agent-rework")).strip(),
        blocked=str(label_map.get("blocked", "blocked")).strip(),
    )
    tracker = TrackerConfig(
        kind=tracker_kind,
        repo=(str(tracker_map["repo"]).strip() if tracker_map.get("repo") else None),
        labels=labels,
    )

    polling_map = dict(config_map.get("polling") or {})
    polling = PollingConfig(
        interval_seconds=_coerce_positive_int(
            polling_map.get("interval_seconds", 30),
            default=30,
            field_name="polling.interval_seconds",
        )
    )

    workspace_map = dict(config_map.get("workspace") or {})
    workspace_root = _expand_path(
        str(workspace_map.get("root", ".symphony/workspaces")),
        base_dir=workflow_path.parent,
    )
    logs_root = _expand_path(
        str(workspace_map.get("logs_root", ".symphony/logs")),
        base_dir=workflow_path.parent,
    )
    workspace = WorkspaceConfig(
        root=workspace_root or workflow_path.parent / ".symphony" / "workspaces",
        base_branch=str(workspace_map.get("base_branch", "develop")).strip() or "develop",
        logs_root=logs_root,
    )

    hooks_map = dict(config_map.get("hooks") or {})
    hooks = HookConfig(
        after_create=hooks_map.get("after_create"),
        before_run=hooks_map.get("before_run"),
        after_run=hooks_map.get("after_run"),
        timeout_seconds=_coerce_positive_int(
            hooks_map.get("timeout_seconds", 900),
            default=900,
            field_name="hooks.timeout_seconds",
        ),
    )

    validation_map = dict(config_map.get("validation") or {})
    commands = validation_map.get("commands") or ("ruff check .", "pytest")
    if isinstance(commands, str):
        commands = [commands]
    if not isinstance(commands, (list, tuple)) or not all(
        isinstance(item, str) for item in commands
    ):
        raise WorkflowLoadError("validation.commands must be a list of shell command strings.")
    validation = ValidationConfig(
        commands=tuple(command.strip() for command in commands if command.strip())
    )

    agent_map = dict(config_map.get("agent") or {})
    agent = AgentConfig(
        max_concurrent_agents=_coerce_positive_int(
            agent_map.get("max_concurrent_agents", 1),
            default=1,
            field_name="agent.max_concurrent_agents",
        ),
        retry_backoff_seconds=_coerce_positive_int(
            agent_map.get("retry_backoff_seconds", 10),
            default=10,
            field_name="agent.retry_backoff_seconds",
        ),
        max_retry_backoff_seconds=_coerce_positive_int(
            agent_map.get("max_retry_backoff_seconds", 300),
            default=300,
            field_name="agent.max_retry_backoff_seconds",
        ),
    )
    if agent.max_concurrent_agents != 1:
        raise WorkflowLoadError(
            "PersonaPort Symphony runner currently supports max_concurrent_agents=1 only."
        )

    pull_request_map = dict(config_map.get("pull_request") or {})
    high_risk_paths = pull_request_map.get("high_risk_paths") or PullRequestConfig().high_risk_paths
    if isinstance(high_risk_paths, str):
        high_risk_paths = [high_risk_paths]
    if not isinstance(high_risk_paths, (list, tuple)) or not all(
        isinstance(item, str) for item in high_risk_paths
    ):
        raise WorkflowLoadError("pull_request.high_risk_paths must be a list of path prefixes.")
    allowed_paths = pull_request_map.get("allowed_paths") or PullRequestConfig().allowed_paths
    if isinstance(allowed_paths, str):
        allowed_paths = [allowed_paths]
    if not isinstance(allowed_paths, (list, tuple)) or not all(
        isinstance(item, str) for item in allowed_paths
    ):
        raise WorkflowLoadError("pull_request.allowed_paths must be a list of path prefixes.")
    pull_request = PullRequestConfig(
        auto_merge=bool(pull_request_map.get("auto_merge", False)),
        merge_method=str(pull_request_map.get("merge_method", "squash")).strip() or "squash",
        delete_branch=bool(pull_request_map.get("delete_branch", True)),
        checks_timeout_seconds=_coerce_positive_int(
            pull_request_map.get("checks_timeout_seconds", 1800),
            default=1800,
            field_name="pull_request.checks_timeout_seconds",
        ),
        checks_poll_seconds=_coerce_positive_int(
            pull_request_map.get("checks_poll_seconds", 15),
            default=15,
            field_name="pull_request.checks_poll_seconds",
        ),
        high_risk_paths=tuple(item.strip() for item in high_risk_paths if item.strip()),
        allowed_paths=tuple(item.strip() for item in allowed_paths if item.strip()),
    )
    if pull_request.merge_method not in {"merge", "squash", "rebase"}:
        raise WorkflowLoadError(
            "pull_request.merge_method must be one of: merge, squash, rebase."
        )

    codex_map = dict(config_map.get("codex") or {})
    sandbox_policy = dict(codex_map.get("turn_sandbox_policy") or {})
    if not sandbox_policy:
        sandbox_policy = {
            "type": "workspaceWrite",
            "networkAccess": False,
            "writableRoots": [],
        }
    codex = CodexConfig(
        command=str(codex_map.get("command", "codex app-server")).strip(),
        model=(str(codex_map["model"]).strip() if codex_map.get("model") else None),
        model_provider=(
            str(codex_map["model_provider"]).strip()
            if codex_map.get("model_provider")
            else None
        ),
        approval_policy=str(codex_map.get("approval_policy", "never")).strip(),
        thread_sandbox=str(codex_map.get("thread_sandbox", "workspace-write")).strip(),
        turn_sandbox_policy=sandbox_policy,
        turn_timeout_seconds=_coerce_positive_int(
            codex_map.get("turn_timeout_seconds", 1800),
            default=1800,
            field_name="codex.turn_timeout_seconds",
        ),
        read_timeout_seconds=_coerce_positive_int(
            codex_map.get("read_timeout_seconds", 5),
            default=5,
            field_name="codex.read_timeout_seconds",
        ),
    )

    return WorkflowConfig(
        workflow_path=workflow_path,
        prompt_template=prompt_body,
        tracker=tracker,
        polling=polling,
        workspace=workspace,
        hooks=hooks,
        validation=validation,
        agent=agent,
        pull_request=pull_request,
        codex=codex,
        raw_config=config_map,
    )


def render_prompt(workflow: WorkflowConfig, issue: GitHubIssue, attempt: int | None = None) -> str:
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    try:
        template = environment.from_string(workflow.prompt_template)
        return template.render(issue=issue, attempt=attempt)
    except TemplateError as exc:
        raise WorkflowLoadError(f"Failed to render workflow prompt: {exc}") from exc


class WorkflowManager:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.current = load_workflow(self.path)
        self._mtime_ns = self.path.stat().st_mtime_ns

    def reload_if_changed(self) -> WorkflowConfig | None:
        current_mtime = self.path.stat().st_mtime_ns
        if current_mtime == self._mtime_ns:
            return None
        self.current = load_workflow(self.path)
        self._mtime_ns = current_mtime
        return self.current

    def with_poll_interval(self, seconds: int) -> WorkflowConfig:
        updated = replace(self.current, polling=PollingConfig(interval_seconds=seconds))
        self.current = updated
        return updated
