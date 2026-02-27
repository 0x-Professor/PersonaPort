from __future__ import annotations

from pathlib import Path

from personaport.models import MigrationArtifact
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
