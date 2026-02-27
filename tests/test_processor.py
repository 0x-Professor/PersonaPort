from __future__ import annotations

import json
import zipfile
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


def test_processor_parses_chatgpt_chat_html(tmp_path: Path) -> None:
    html_path = tmp_path / "chat.html"
    html_path.write_text(
        """
        <html>
          <head><title>ChatGPT - Migration Test</title></head>
          <body>
            <article><h5 class="sr-only">You said:</h5><div>Hello there</div></article>
            <article><h5 class="sr-only">ChatGPT said:</h5><div>General Kenobi</div></article>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    processor = ConversationProcessor(console=get_console())
    conversations = processor.load_conversations(html_path, source_platform=Platform.CHATGPT)

    assert len(conversations) == 1
    assert conversations[0].title == "Migration Test"
    assert [msg.role for msg in conversations[0].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


def test_processor_parses_zip_with_chat_html(tmp_path: Path) -> None:
    html_content = """
        <html>
          <head><title>ChatGPT - Zip Test</title></head>
          <body>
            <article><h5 class="sr-only">You said:</h5><div>Message A</div></article>
            <article><h5 class="sr-only">ChatGPT said:</h5><div>Message B</div></article>
          </body>
        </html>
    """
    zip_path = tmp_path / "chatgpt_export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("chat.html", html_content)

    processor = ConversationProcessor(console=get_console())
    conversations = processor.load_conversations(zip_path, source_platform=Platform.CHATGPT)

    assert len(conversations) == 1
    assert conversations[0].title == "Zip Test"
    assert len(conversations[0].messages) == 2


def test_processor_parses_chatgpt_sharded_zip_all_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "chatgpt_export.zip"

    shard_0 = [
        {
            "id": "conv-0",
            "title": "Shard 0",
            "create_time": 1000,
            "update_time": 1001,
            "mapping": {
                "node-0": {
                    "id": "node-0",
                    "parent": None,
                    "children": [],
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1000,
                        "content": {"parts": ["hello from shard 0"]},
                    },
                }
            },
        }
    ]
    shard_1 = [
        {
            "id": "conv-1",
            "title": "Shard 1",
            "create_time": 2000,
            "update_time": 2001,
            "mapping": {
                "node-1": {
                    "id": "node-1",
                    "parent": None,
                    "children": [],
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 2000,
                        "content": {"parts": ["hello from shard 1"]},
                    },
                }
            },
        }
    ]

    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("conversations-000.json", json.dumps(shard_0))
        archive.writestr("conversations-001.json", json.dumps(shard_1))

    processor = ConversationProcessor(console=get_console())
    conversations = processor.load_conversations(zip_path, source_platform=Platform.CHATGPT)

    assert len(conversations) == 2
    assert {conversation.id for conversation in conversations} == {"conv-0", "conv-1"}
