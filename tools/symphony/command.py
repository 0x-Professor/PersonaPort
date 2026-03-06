from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import CommandResult


class CommandCancelledError(RuntimeError):
    """Raised when a command is cancelled before completion."""


class CommandTimeoutError(RuntimeError):
    """Raised when a command exceeds its timeout."""


@dataclass
class CommandExecutionError(RuntimeError):
    result: CommandResult

    def __str__(self) -> str:
        return (
            f"Command failed with exit code {self.result.returncode}: {self.result.command}\n"
            f"{self.result.stderr.strip()}"
        ).strip()


class ShellCommandRunner:
    def run(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        started_at = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=True,
        )
        payload: dict[str, object] = {}
        error_holder: dict[str, BaseException] = {}

        def _communicate() -> None:
            try:
                stdout, stderr = process.communicate(input=input_text)
                payload["stdout"] = stdout
                payload["stderr"] = stderr
            except BaseException as exc:  # pragma: no cover - defensive
                error_holder["error"] = exc

        worker = threading.Thread(target=_communicate, daemon=True)
        worker.start()
        deadline = (
            time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        )

        try:
            while worker.is_alive():
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise CommandCancelledError(f"Cancelled command: {command}")
                if deadline is not None and time.monotonic() > deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise CommandTimeoutError(
                        f"Command timed out after {timeout_seconds}s: {command}"
                    )
                time.sleep(0.05)
        finally:
            worker.join(timeout=1)

        if "error" in error_holder:  # pragma: no cover - defensive
            raise RuntimeError("Command execution crashed") from error_holder["error"]

        result = CommandResult(
            command=command,
            returncode=process.returncode or 0,
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            duration_seconds=time.monotonic() - started_at,
        )
        if check and result.returncode != 0:
            raise CommandExecutionError(result)
        return result

    def run_json(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> object:
        result = self.run(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            check=True,
        )
        return json.loads(result.stdout or "null")
