from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import keyring
import yaml
from keyring.errors import KeyringError
from pydantic import BaseModel, ConfigDict

from personaport.models import Platform

SERVICE_NAME = "personaport"
CONFIG_FILENAME = "config.yaml"


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    home_dir: Path
    db_path: Path
    sessions_dir: Path
    exports_dir: Path
    processed_dir: Path
    default_summary_model: str = "ollama/llama3.1:8b"
    max_context_chars: int = 22000
    max_chunk_chars: int = 6000
    litellm_timeout_seconds: int = 120


class ConfigManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self._home_dir = Path(
            os.getenv("PERSONAPORT_HOME", str(Path.home() / ".personaport"))
        ).expanduser()
        self._config_path = (
            config_path.expanduser() if config_path else self._home_dir / CONFIG_FILENAME
        )

    @property
    def config_path(self) -> Path:
        return self._config_path

    def load(self) -> AppConfig:
        if not self._config_path.exists():
            config = self._default_config()
            self.save(config)
            return config

        raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        config = AppConfig(
            home_dir=Path(raw.get("home_dir", self._home_dir)),
            db_path=Path(raw.get("db_path", self._home_dir / "personaport.db")),
            sessions_dir=Path(raw.get("sessions_dir", self._home_dir / "sessions")),
            exports_dir=Path(raw.get("exports_dir", self._home_dir / "exports")),
            processed_dir=Path(raw.get("processed_dir", self._home_dir / "processed")),
            default_summary_model=raw.get(
                "default_summary_model", "ollama/llama3.1:8b"
            ),
            max_context_chars=int(raw.get("max_context_chars", 22000)),
            max_chunk_chars=int(raw.get("max_chunk_chars", 6000)),
            litellm_timeout_seconds=int(raw.get("litellm_timeout_seconds", 120)),
        )
        self.ensure_directories(config)
        return config

    def save(self, config: AppConfig) -> None:
        self.ensure_directories(config)
        payload = {
            "home_dir": str(config.home_dir),
            "db_path": str(config.db_path),
            "sessions_dir": str(config.sessions_dir),
            "exports_dir": str(config.exports_dir),
            "processed_dir": str(config.processed_dir),
            "default_summary_model": config.default_summary_model,
            "max_context_chars": config.max_context_chars,
            "max_chunk_chars": config.max_chunk_chars,
            "litellm_timeout_seconds": config.litellm_timeout_seconds,
        }
        self._config_path.write_text(
            yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
        )

    def ensure_directories(self, config: AppConfig) -> None:
        config.home_dir.mkdir(parents=True, exist_ok=True)
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
        config.exports_dir.mkdir(parents=True, exist_ok=True)
        config.processed_dir.mkdir(parents=True, exist_ok=True)
        if not config.db_path.parent.exists():
            config.db_path.parent.mkdir(parents=True, exist_ok=True)

    def session_state_path(self, config: AppConfig, platform: Platform | str) -> Path:
        platform_name = platform.value if isinstance(platform, Platform) else platform
        return config.sessions_dir / f"{platform_name}_state.json"

    def set_secret(self, key: str, value: str) -> bool:
        try:
            keyring.set_password(SERVICE_NAME, key, value)
            return True
        except KeyringError:
            return False

    def get_secret(self, key: str) -> str | None:
        try:
            return keyring.get_password(SERVICE_NAME, key)
        except KeyringError:
            return None

    def _default_config(self) -> AppConfig:
        return AppConfig(
            home_dir=self._home_dir,
            db_path=self._home_dir / "personaport.db",
            sessions_dir=self._home_dir / "sessions",
            exports_dir=self._home_dir / "exports",
            processed_dir=self._home_dir / "processed",
        )


def merge_config_overrides(config: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    payload = config.model_dump()
    for key, value in overrides.items():
        if value is not None and key in payload:
            payload[key] = value
    return AppConfig(**payload)
