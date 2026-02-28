from __future__ import annotations

from pathlib import Path

from personaport.models import (
    ChatMessage,
    Conversation,
    MessageRole,
    MigrationArtifact,
    PersonaProfile,
    Platform,
)
from personaport.transfer import MAX_KNOWLEDGE_CHUNK_BYTES, TransferService
from personaport.utils.console import get_console


def test_write_artifact_chunks_large_knowledge(tmp_path: Path) -> None:
    service = TransferService(console=get_console(), processed_dir=tmp_path)
    large_text = "A" * (MAX_KNOWLEDGE_CHUNK_BYTES + 1024)
    artifact = MigrationArtifact(
        prompt_markdown="# prompt",
        knowledge_text=large_text,
        raw_json={"k": "v"},
    )

    output = service.write_artifact(
        artifact,
        target_platform="generic",
        output_dir=tmp_path,
        prefix="chunk-test",
    )

    chunk_keys = sorted(key for key in output if key.startswith("knowledge_chunk_"))
    assert len(chunk_keys) >= 2
    for key in chunk_keys:
        assert Path(output[key]).exists()


def test_build_artifact_uses_context_map_and_compact_knowledge(tmp_path: Path) -> None:
    service = TransferService(console=get_console(), processed_dir=tmp_path)
    conversation = Conversation(
        id="conv-ctx",
        title="Migration",
        source_platform=Platform.CHATGPT,
        messages=[
            ChatMessage(role=MessageRole.USER, content="Need to migrate auth to FastAPI."),
            ChatMessage(role=MessageRole.ASSISTANT, content="Let's do it in phases."),
        ],
    )
    persona = PersonaProfile(
        name="Persona",
        system_prompt="Be concise.",
        extracted_facts=["Prefers practical plans."],
        style_notes=["Direct tone"],
    )
    context_map = {
        "top_topics": [{"topic": "fastapi", "frequency": 3}],
        "open_tasks": ["Migrate auth module"],
        "priority_threads": [{"title": "Migration", "score": 12, "focus": "auth migration"}],
        "work_profile": [{"area": "backend", "frequency": 2}],
        "goals": ["Ship migration"],
        "decisions": ["Use phased rollout"],
        "stats": {"conversation_count": 1, "message_count": 2, "user_message_count": 1},
    }

    artifact = service.build_artifact(
        conversation,
        persona,
        condensed_history="Short summary",
        context_map=context_map,
        target_platform=Platform.GENERIC,
    )

    assert "Top Topics" in artifact.knowledge_text
    assert "Full Normalized History" not in artifact.knowledge_text
    assert "PRIORITY THREADS" in artifact.prompt_markdown
