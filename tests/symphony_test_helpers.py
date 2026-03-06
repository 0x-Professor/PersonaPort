from __future__ import annotations

from collections import deque
from pathlib import Path

from tools.symphony.models import CommandResult


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path | None]] = []
        self.command_results: dict[str, deque[CommandResult | Exception]] = {}
        self.json_results: dict[str, deque[object | Exception]] = {}

    def add_command_result(
        self,
        needle: str,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        self.command_results.setdefault(needle, deque()).append(
            CommandResult(
                command=needle,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=0.01,
            )
        )

    def add_json_result(self, needle: str, payload: object) -> None:
        self.json_results.setdefault(needle, deque()).append(payload)

    def run(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event=None,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        del timeout_seconds, cancel_event, check, input_text
        self.calls.append((command, cwd))
        for needle, queue in self.command_results.items():
            if needle in command and queue:
                result = queue.popleft()
                if isinstance(result, Exception):
                    raise result
                return result
        return CommandResult(
            command=command,
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )

    def run_json(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event=None,
    ) -> object:
        del timeout_seconds, cancel_event
        self.calls.append((command, cwd))
        for needle, queue in self.json_results.items():
            if needle in command and queue:
                result = queue.popleft()
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"No fake JSON payload configured for command: {command}")
