from __future__ import annotations

import io
from pathlib import Path

from tools.symphony.codex_app_server import CodexAppServerClient
from tools.symphony.models import CodexConfig


class _DummyProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self._returncode = None

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self._returncode = 0

    def wait(self, timeout: int | None = None) -> int:
        del timeout
        self._returncode = 0
        return 0

    def kill(self) -> None:
        self._returncode = 1


def test_codex_client_uses_workspace_cwd(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_popen(command, cwd, stdin, stdout, stderr, text, encoding, shell):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["stdin"] = stdin
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["text"] = text
        captured["encoding"] = encoding
        captured["shell"] = shell
        return _DummyProcess()

    monkeypatch.setattr("tools.symphony.codex_app_server.subprocess.Popen", _fake_popen)
    client = CodexAppServerClient(CodexConfig(command="codex app-server"))
    responses = [
        {},
        {"thread": {"id": "thread-1"}},
        {"turn": {"id": "turn-1"}},
    ]

    def _fake_request(method, params, cancel_event=None):
        del method, params, cancel_event
        return responses.pop(0)

    def _fake_wait(cancel_event=None):
        del cancel_event
        client._turn_status = "completed"  # type: ignore[attr-defined]
        client._turn_id = "turn-1"  # type: ignore[attr-defined]
        client._assistant_chunks = ["done"]  # type: ignore[attr-defined]

    monkeypatch.setattr(client, "_start_stream_threads", lambda: None)
    monkeypatch.setattr(client, "_request", _fake_request)
    monkeypatch.setattr(client, "_wait_for_turn_completion", _fake_wait)

    result = client.run_prompt(prompt="hello", workspace=tmp_path)

    assert captured["command"] == "codex app-server"
    assert captured["cwd"] == str(tmp_path)
    assert captured["shell"] is True
    assert result.thread_id == "thread-1"
    assert result.turn_id == "turn-1"
    assert result.assistant_text == "done"
