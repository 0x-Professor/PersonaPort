from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, Enum):
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"
    GENERIC = "generic"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        return value.strip()


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = "Untitled Conversation"
    source_platform: Platform | str = Platform.GENERIC
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    messages: list[ChatMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def infer_updated_at(self) -> "Conversation":
        if self.messages:
            latest = max(
                (msg.timestamp for msg in self.messages if msg.timestamp is not None),
                default=None,
            )
            if latest is not None:
                self.updated_at = latest
        return self

    def to_history_text(self) -> str:
        lines: list[str] = []
        for message in self.messages:
            prefix = message.role.value.capitalize()
            timestamp = (
                f"[{message.timestamp.isoformat()}] " if message.timestamp else ""
            )
            lines.append(f"{timestamp}{prefix}: {message.content}")
        return "\n\n".join(lines)


class PersonaProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = "Default Persona"
    system_prompt: str
    extracted_facts: list[str] = Field(default_factory=list)
    style_notes: list[str] = Field(default_factory=list)
    source_conversation_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ProcessedHistory(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    conversation_id: str
    target_platform: Platform | str
    condensed_history: str
    full_history: str
    persona_prompt: str
    output_files: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MigrationArtifact(BaseModel):
    prompt_markdown: str
    knowledge_text: str
    raw_json: dict[str, Any]
