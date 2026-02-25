from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from personaport.db import ConversationCache
from personaport.models import ChatMessage, Conversation, MessageRole, Platform


def test_db_round_trip_conversation(tmp_path: Path) -> None:
    db_path = tmp_path / "personaport.db"
    cache = ConversationCache(db_path)

    conversation = Conversation(
        id="conv-1",
        title="Test Conversation",
        source_platform=Platform.CHATGPT,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        messages=[
            ChatMessage(role=MessageRole.USER, content="hello"),
            ChatMessage(role=MessageRole.ASSISTANT, content="world"),
        ],
    )

    cache.save_conversation(conversation)
    loaded = cache.get_conversation("conv-1")
    assert loaded is not None
    assert loaded.id == "conv-1"
    assert len(loaded.messages) == 2
