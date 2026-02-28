from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from rich.console import Console

from personaport.browser.manager import BrowserManager
from personaport.browser.platforms import get_platform_adapter
from personaport.models import Conversation, MigrationArtifact, PersonaProfile, Platform

MAX_KNOWLEDGE_CHUNK_BYTES = 4_000_000


class TransferService:
    def __init__(self, *, console: Console, processed_dir: Path) -> None:
        self.console = console
        self.processed_dir = processed_dir
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.templates = Environment(
            loader=PackageLoader("personaport", "templates"),
            autoescape=select_autoescape(default_for_string=False),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build_artifact(
        self,
        conversation: Conversation,
        persona: PersonaProfile,
        *,
        condensed_history: str,
        context_map: dict[str, Any] | None = None,
        target_platform: Platform | str,
    ) -> MigrationArtifact:
        target = target_platform.value if isinstance(target_platform, Platform) else str(target_platform)
        template_name = self._template_for_target(target)
        template = self.templates.get_template(template_name)
        full_history = conversation.to_history_text()
        context_map = context_map or {}

        context = {
            "persona_system_prompt": persona.system_prompt.strip(),
            "persona_facts": persona.extracted_facts,
            "persona_style_notes": persona.style_notes,
            "conversation_title": conversation.title,
            "condensed_history": condensed_history.strip(),
            "full_history": full_history.strip(),
            "context_map": context_map,
            "continue_instruction": (
                "Continue from the latest unresolved task and keep response style consistent."
            ),
        }
        prompt_markdown = template.render(**context).strip()
        knowledge_text = self._build_knowledge_text(
            persona,
            conversation,
            condensed_history,
            context_map,
        )
        raw_json = {
            "target_platform": target,
            "persona": persona.model_dump(mode="json"),
            "conversation": conversation.model_dump(mode="json"),
            "context_map": context_map,
            "condensed_history": condensed_history,
            "prompt_markdown": prompt_markdown,
            "knowledge_text": knowledge_text,
        }
        return MigrationArtifact(
            prompt_markdown=prompt_markdown,
            knowledge_text=knowledge_text,
            raw_json=raw_json,
        )

    def write_artifact(
        self,
        artifact: MigrationArtifact,
        *,
        target_platform: Platform | str,
        output_dir: Path | None = None,
        prefix: str | None = None,
    ) -> dict[str, str]:
        target = target_platform.value if isinstance(target_platform, Platform) else str(target_platform)
        destination = output_dir or self.processed_dir
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        basename = prefix or f"migrate_to_{target}_{stamp}"

        prompt_path = destination / f"{basename}.md"
        knowledge_path = destination / f"{basename}_knowledge.txt"
        json_path = destination / f"{basename}_full_json.json"

        prompt_path.write_text(artifact.prompt_markdown, encoding="utf-8")
        knowledge_path.write_text(artifact.knowledge_text, encoding="utf-8")
        json_path.write_text(json.dumps(artifact.raw_json, indent=2), encoding="utf-8")

        output_files: dict[str, str] = {
            "prompt_markdown": str(prompt_path),
            "knowledge_text": str(knowledge_path),
            "full_json": str(json_path),
        }
        knowledge_size = len(artifact.knowledge_text.encode("utf-8"))
        if knowledge_size > MAX_KNOWLEDGE_CHUNK_BYTES:
            chunks = self._chunk_text_for_upload(
                artifact.knowledge_text,
                max_bytes=MAX_KNOWLEDGE_CHUNK_BYTES,
            )
            for index, chunk in enumerate(chunks, start=1):
                chunk_path = destination / f"{basename}_knowledge_part{index:03d}.txt"
                chunk_path.write_text(chunk, encoding="utf-8")
                output_files[f"knowledge_chunk_{index:03d}"] = str(chunk_path)
        return output_files

    def inject_to_target(
        self,
        *,
        target_platform: Platform | str,
        state_path: Path,
        prompt_text: str,
        knowledge_files: list[Path] | None = None,
        headless: bool = False,
    ) -> None:
        adapter = get_platform_adapter(target_platform)
        manager = BrowserManager(state_path=state_path, headless=headless)
        with manager.open() as runtime:
            page = runtime.context.new_page()
            adapter.inject_payload(page, prompt_text, knowledge_files, self.console)

    def _template_for_target(self, target_platform: str) -> str:
        mapping = {
            Platform.CLAUDE.value: "claude_project.md.j2",
            Platform.CHATGPT.value: "chatgpt_migration.md.j2",
            Platform.GEMINI.value: "gemini_migration.md.j2",
            Platform.GENERIC.value: "generic_migration.md.j2",
        }
        return mapping.get(target_platform, "generic_migration.md.j2")

    def _build_knowledge_text(
        self,
        persona: PersonaProfile,
        conversation: Conversation,
        condensed_history: str,
        context_map: dict[str, Any],
    ) -> str:
        facts = "\n".join(f"- {fact}" for fact in persona.extracted_facts) or "- No facts extracted"
        style_notes = "\n".join(f"- {note}" for note in persona.style_notes) or "- No style notes extracted"
        topic_lines = "\n".join(
            f"- {item['topic']} ({item['frequency']})"
            for item in context_map.get("top_topics", [])
            if isinstance(item, dict) and item.get("topic")
        ) or "- No high-frequency topics detected"
        work_lines = "\n".join(
            f"- {item['area']} ({item['frequency']})"
            for item in context_map.get("work_profile", [])
            if isinstance(item, dict) and item.get("area")
        ) or "- No work profile inferred"
        task_lines = "\n".join(
            f"- {line}" for line in context_map.get("open_tasks", []) if isinstance(line, str)
        ) or "- No open tasks extracted"
        goal_lines = "\n".join(
            f"- {line}" for line in context_map.get("goals", []) if isinstance(line, str)
        ) or "- No goals extracted"
        decision_lines = "\n".join(
            f"- {line}" for line in context_map.get("decisions", []) if isinstance(line, str)
        ) or "- No decisions extracted"

        thread_lines = [
            f"- {item.get('title', 'Untitled')} | score={item.get('score', 0)} | "
            f"messages={item.get('message_count', 0)} | focus={item.get('focus', '')}"
            for item in context_map.get("priority_threads", [])
            if isinstance(item, dict)
        ]
        priority_threads = "\n".join(thread_lines) or "- No priority threads ranked"

        stats = context_map.get("stats", {}) if isinstance(context_map, dict) else {}
        stats_block = (
            f"Conversations: {stats.get('conversation_count', 0)}\n"
            f"Messages: {stats.get('message_count', 0)}\n"
            f"User messages: {stats.get('user_message_count', 0)}"
        )
        return (
            f"Persona Name: {persona.name}\n\n"
            f"System Prompt:\n{persona.system_prompt}\n\n"
            f"Extracted Facts:\n{facts}\n\n"
            f"Style Notes:\n{style_notes}\n\n"
            f"Conversation Stats:\n{stats_block}\n\n"
            f"Top Topics (frequency-ranked):\n{topic_lines}\n\n"
            f"Work Profile:\n{work_lines}\n\n"
            f"Open Tasks:\n{task_lines}\n\n"
            f"User Goals:\n{goal_lines}\n\n"
            f"Key Decisions:\n{decision_lines}\n\n"
            f"Priority Threads:\n{priority_threads}\n\n"
            f"Conversation Title: {conversation.title}\n\n"
            f"Condensed History:\n{condensed_history}\n"
        )

    def _chunk_text_for_upload(self, text: str, *, max_bytes: int) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0

        for line in text.splitlines(keepends=True):
            line_bytes = len(line.encode("utf-8"))
            if line_bytes > max_bytes:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_bytes = 0
                chunks.extend(self._split_large_line(line, max_bytes=max_bytes))
                continue

            if current_bytes + line_bytes > max_bytes and current:
                chunks.append("".join(current))
                current = [line]
                current_bytes = line_bytes
            else:
                current.append(line)
                current_bytes += line_bytes

        if current:
            chunks.append("".join(current))
        return chunks

    def _split_large_line(self, line: str, *, max_bytes: int) -> list[str]:
        parts: list[str] = []
        buffer: list[str] = []
        buffer_bytes = 0
        for char in line:
            char_bytes = len(char.encode("utf-8"))
            if buffer and buffer_bytes + char_bytes > max_bytes:
                parts.append("".join(buffer))
                buffer = [char]
                buffer_bytes = char_bytes
            else:
                buffer.append(char)
                buffer_bytes += char_bytes
        if buffer:
            parts.append("".join(buffer))
        return parts
