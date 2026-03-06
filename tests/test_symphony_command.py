from __future__ import annotations

from pathlib import Path

from tools.symphony.command import ShellCommandRunner


class _DummyProcess:
    def __init__(self) -> None:
        self.returncode = 0

    def communicate(self, input=None):
        del input
        return "ok", ""

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.returncode = 1


def test_shell_command_runner_disables_shell_for_argument_lists(monkeypatch, tmp_path: Path) -> None:
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

    monkeypatch.setattr("tools.symphony.command.subprocess.Popen", _fake_popen)

    result = ShellCommandRunner().run(["git", "status", "--short"], cwd=tmp_path)

    assert captured["command"] == ["git", "status", "--short"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["shell"] is False
    assert result.command == "git status --short"
