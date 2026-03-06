from __future__ import annotations

import subprocess
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

from tools.symphony.models import CommandResult

CommandSpec: TypeAlias = str | Sequence[str]


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path | None]] = []
        self.raw_calls: list[tuple[CommandSpec, Path | None]] = []
        self.command_results: dict[str, deque[CommandResult | Exception]] = {}
        self.json_results: dict[str, deque[object | Exception]] = {}

    @staticmethod
    def _display_command(command: CommandSpec) -> str:
        if isinstance(command, str):
            return command
        return subprocess.list2cmdline([str(part) for part in command])

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
        command: CommandSpec,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event=None,
        check: bool = True,
        input_text: str | None = None,
        shell: bool = False,
    ) -> CommandResult:
        del timeout_seconds, cancel_event, check, input_text, shell
        display_command = self._display_command(command)
        self.raw_calls.append((command, cwd))
        self.calls.append((display_command, cwd))
        for needle, queue in self.command_results.items():
            if needle in display_command and queue:
                result = queue.popleft()
                if isinstance(result, Exception):
                    raise result
                return result
        return CommandResult(
            command=display_command,
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
        )

    def run_json(
        self,
        command: CommandSpec,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event=None,
        shell: bool = False,
    ) -> object:
        del timeout_seconds, cancel_event, shell
        display_command = self._display_command(command)
        self.raw_calls.append((command, cwd))
        self.calls.append((display_command, cwd))
        for needle, queue in self.json_results.items():
            if needle in display_command and queue:
                result = queue.popleft()
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"No fake JSON payload configured for command: {display_command}")
