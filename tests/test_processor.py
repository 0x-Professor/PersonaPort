from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from personaport.models import ChatMessage, Conversation, MessageRole, Platform
from personaport.processor import ConversationProcessor
from personaport.utils.console import get_console


def test_processor_parses_personaport_json(tmp_path: Path) -> None:
    export_path = tmp_path / "export.json"
    payload = [
        {
            "id": "abc123",
            "title": "Imported",
            "source_platform": "chatgpt",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "messages": [
                {"role": "user", "content": "I prefer concise responses."},
                {"role": "assistant", "content": "Understood."},
            ],
            "metadata": {},
        }
    ]
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    processor = ConversationProcessor(console=get_console())
    conversations = processor.load_conversations(export_path)

    assert len(conversations) == 1
    assert conversations[0].id == "abc123"
    assert conversations[0].messages[0].role == MessageRole.USER


def test_persona_extraction_heuristic_fallback() -> None:
    processor = ConversationProcessor(console=get_console())
    conversation = Conversation(
        id="conv-x",
        title="Persona Test",
        source_platform=Platform.CLAUDE,
        messages=[
            ChatMessage(
                role=MessageRole.USER,
                content="I prefer practical answers and I love Rust.",
            ),
            ChatMessage(role=MessageRole.ASSISTANT, content="Great."),
        ],
    )
    persona = processor.extract_persona([conversation], use_llm=False)

    assert "preferences" in persona.system_prompt.lower() or "prefer" in persona.system_prompt.lower()
    assert any("rust" in fact.lower() for fact in persona.extracted_facts)
