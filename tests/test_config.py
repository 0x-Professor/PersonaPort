from __future__ import annotations

from pathlib import Path

from personaport.config import ConfigManager


def test_config_manager_creates_default_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAPORT_HOME", str(tmp_path / "home"))
    manager = ConfigManager()
    config = manager.load()

    assert manager.config_path.exists()
    assert config.home_dir.exists()
    assert config.sessions_dir.exists()
    assert config.exports_dir.exists()
    assert config.processed_dir.exists()
