from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .models import CodexConfig, CodexTurnResult


class CodexAppServerError(RuntimeError):
    """Raised when the Codex app-server protocol fails."""


class CodexAppServerClient:
    def __init__(self, config: CodexConfig) -> None:
        self.config = config
        self._stdout_messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._responses: dict[int, dict[str, Any]] = {}
        self._assistant_chunks: list[str] = []
        self._stderr_lines: list[str] = []
        self._token_usage: dict[str, int] = {}
        self._turn_status: str | None = None
        self._turn_id: str | None = None
        self._turn_error: str | None = None
        self._id_counter = 0
        self._process: subprocess.Popen[str] | None = None

    def run_prompt(
        self,
        *,
        prompt: str,
        workspace: Path,
        cancel_event: threading.Event | None = None,
    ) -> CodexTurnResult:
        self._reset_state()
        self._process = subprocess.Popen(
            self.config.command,
            cwd=str(workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=True,
        )
        self._start_stream_threads()
        try:
            self._request(
                "initialize",
                {
                    "clientInfo": {"name": "PersonaPort Symphony", "version": "0.1"},
                    "capabilities": {"experimentalApi": False},
                },
                cancel_event=cancel_event,
            )
            self._notify({"method": "initialized"})
            thread_response = self._request(
                "thread/start",
                self._thread_start_params(workspace),
                cancel_event=cancel_event,
            )
            thread = dict(thread_response.get("thread") or {})
            thread_id = str(thread.get("id", ""))
            if not thread_id:
                raise CodexAppServerError("Codex thread/start response did not include a thread id.")
            self._request(
                "turn/start",
                self._turn_start_params(thread_id=thread_id, prompt=prompt, workspace=workspace),
                cancel_event=cancel_event,
            )
            self._wait_for_turn_completion(cancel_event=cancel_event)
            if self._turn_status != "completed":
                raise CodexAppServerError(self._turn_error or "Codex turn failed.")
            return CodexTurnResult(
                thread_id=thread_id,
                turn_id=self._turn_id or "",
                assistant_text="".join(self._assistant_chunks).strip(),
                status=self._turn_status or "completed",
                error_message=self._turn_error,
                token_usage=dict(self._token_usage),
                stderr_lines=tuple(self._stderr_lines),
            )
        finally:
            self._shutdown()

    def _thread_start_params(self, workspace: Path) -> dict[str, Any]:
        return {
            "cwd": str(workspace),
            "approvalPolicy": self.config.approval_policy,
            "sandbox": self.config.thread_sandbox,
            "model": self.config.model,
            "modelProvider": self.config.model_provider,
            "personality": "pragmatic",
        }

    def _turn_start_params(
        self,
        *,
        thread_id: str,
        prompt: str,
        workspace: Path,
    ) -> dict[str, Any]:
        sandbox_policy = dict(self.config.turn_sandbox_policy)
        if sandbox_policy.get("type") == "workspaceWrite":
            sandbox_policy["writableRoots"] = [str(workspace)]
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "sandboxPolicy": sandbox_policy,
            "approvalPolicy": self.config.approval_policy,
            "cwd": str(workspace),
            "model": self.config.model,
            "personality": "pragmatic",
            "summary": "concise",
        }

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        self._id_counter += 1
        request_id = self._id_counter
        self._send({"id": request_id, "method": method, "params": params})
        return self._wait_for_response(request_id, cancel_event=cancel_event)

    def _notify(self, payload: dict[str, Any]) -> None:
        self._send(payload)

    def _send(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise CodexAppServerError("Codex app-server process is not available.")
        self._process.stdin.write(json.dumps(payload) + "\n")
        self._process.stdin.flush()

    def _wait_for_response(
        self,
        request_id: int,
        *,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.read_timeout_seconds
        while time.monotonic() <= deadline:
            if request_id in self._responses:
                response = self._responses.pop(request_id)
                if "error" in response:
                    raise CodexAppServerError(str(response["error"]))
                return dict(response["result"])
            self._pump_messages(cancel_event=cancel_event, timeout=0.2)
        raise CodexAppServerError(
            f"Timed out waiting for Codex response to request {request_id}."
        )

    def _wait_for_turn_completion(
        self,
        *,
        cancel_event: threading.Event | None,
    ) -> None:
        deadline = time.monotonic() + self.config.turn_timeout_seconds
        while self._turn_status is None:
            if time.monotonic() > deadline:
                raise CodexAppServerError("Codex turn timed out.")
            self._pump_messages(cancel_event=cancel_event, timeout=0.2)

    def _pump_messages(
        self,
        *,
        cancel_event: threading.Event | None,
        timeout: float,
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise CodexAppServerError("Codex turn cancelled.")
        try:
            kind, payload = self._stdout_messages.get(timeout=timeout)
        except queue.Empty:
            return
        if kind == "stdout-closed":
            if self._turn_status is None:
                raise CodexAppServerError("Codex app-server closed stdout before completing.")
            return
        if kind == "stderr":
            self._stderr_lines.append(str(payload))
            return
        if kind != "stdout":
            return
        assert isinstance(payload, dict)
        self._dispatch_message(payload)

    def _dispatch_message(self, payload: dict[str, Any]) -> None:
        if "id" in payload and "method" in payload:
            self._handle_server_request(payload)
            return
        if "id" in payload:
            self._responses[int(payload["id"])] = payload
            return
        method = payload.get("method")
        params = dict(payload.get("params") or {})
        if method == "item/agentMessage/delta":
            self._assistant_chunks.append(str(params.get("delta", "")))
        elif method == "thread/tokenUsage/updated":
            totals = dict((params.get("tokenUsage") or {}).get("total") or {})
            self._token_usage = {
                "input_tokens": int(totals.get("inputTokens", 0)),
                "output_tokens": int(totals.get("outputTokens", 0)),
                "total_tokens": int(totals.get("totalTokens", 0)),
            }
        elif method == "turn/started":
            turn = dict(params.get("turn") or {})
            self._turn_id = str(turn.get("id", ""))
        elif method == "turn/completed":
            turn = dict(params.get("turn") or {})
            self._turn_id = str(turn.get("id", ""))
            self._turn_status = str(turn.get("status", "failed"))
            error = turn.get("error") or {}
            if isinstance(error, dict):
                self._turn_error = error.get("message")
        elif method == "error":
            message = params.get("message") or params.get("error") or "Codex error"
            raise CodexAppServerError(str(message))

    def _handle_server_request(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method"))
        request_id = payload.get("id")
        if method == "item/commandExecution/requestApproval":
            result = {"decision": "acceptForSession"}
        elif method == "item/fileChange/requestApproval":
            result = {"decision": "acceptForSession"}
        elif method == "item/tool/requestUserInput":
            result = {"answers": {}}
        elif method == "item/tool/call":
            result = {
                "success": False,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": "Dynamic tool calls are disabled in PersonaPort Symphony.",
                    }
                ],
            }
        else:
            self._send(
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unsupported method: {method}"},
                }
            )
            return
        self._send({"id": request_id, "result": result})

    def _start_stream_threads(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        assert self._process.stderr is not None

        def _stdout_worker() -> None:
            for line in self._process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    self._stdout_messages.put(
                        ("stdout", {"method": "error", "params": {"message": str(exc)}})
                    )
                    continue
                self._stdout_messages.put(("stdout", payload))
            self._stdout_messages.put(("stdout-closed", None))

        def _stderr_worker() -> None:
            for line in self._process.stderr:
                text = line.rstrip()
                if text:
                    self._stdout_messages.put(("stderr", text))

        threading.Thread(target=_stdout_worker, daemon=True).start()
        threading.Thread(target=_stderr_worker, daemon=True).start()

    def _shutdown(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _reset_state(self) -> None:
        self._stdout_messages = queue.Queue()
        self._responses = {}
        self._assistant_chunks = []
        self._stderr_lines = []
        self._token_usage = {}
        self._turn_status = None
        self._turn_id = None
        self._turn_error = None
        self._id_counter = 0
