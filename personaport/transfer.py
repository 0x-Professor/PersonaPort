from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from rich.console import Console

from personaport.browser.manager import BrowserManager
from personaport.browser.platforms import get_platform_adapter
from personaport.models import Conversation, MigrationArtifact, PersonaProfile, Platform


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
        target_platform: Platform | str,
    ) -> MigrationArtifact:
        target = target_platform.value if isinstance(target_platform, Platform) else str(target_platform)
        template_name = self._template_for_target(target)
        template = self.templates.get_template(template_name)
        full_history = conversation.to_history_text()

        context = {
            "persona_system_prompt": persona.system_prompt.strip(),
            "persona_facts": persona.extracted_facts,
            "persona_style_notes": persona.style_notes,
            "conversation_title": conversation.title,
            "condensed_history": condensed_history.strip(),
            "full_history": full_history.strip(),
            "continue_instruction": (
                "Continue from the latest unresolved task and keep response style consistent."
            ),
        }
        prompt_markdown = template.render(**context).strip()
        knowledge_text = self._build_knowledge_text(persona, conversation, condensed_history)
        raw_json = {
            "target_platform": target,
            "persona": persona.model_dump(mode="json"),
            "conversation": conversation.model_dump(mode="json"),
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

        return {
            "prompt_markdown": str(prompt_path),
            "knowledge_text": str(knowledge_path),
            "full_json": str(json_path),
        }

    def inject_to_target(
        self,
        *,
        target_platform: Platform | str,
        state_path: Path,
        prompt_text: str,
        knowledge_file: Path | None = None,
        headless: bool = False,
    ) -> None:
        adapter = get_platform_adapter(target_platform)
        manager = BrowserManager(state_path=state_path, headless=headless)
        with manager.open() as runtime:
            page = runtime.context.new_page()
            adapter.inject_payload(page, prompt_text, knowledge_file, self.console)

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
    ) -> str:
        facts = "\n".join(f"- {fact}" for fact in persona.extracted_facts) or "- No facts extracted"
        style_notes = "\n".join(f"- {note}" for note in persona.style_notes) or "- No style notes extracted"
        return (
            f"Persona Name: {persona.name}\n\n"
            f"System Prompt:\n{persona.system_prompt}\n\n"
            f"Extracted Facts:\n{facts}\n\n"
            f"Style Notes:\n{style_notes}\n\n"
            f"Conversation Title: {conversation.title}\n\n"
            f"Condensed History:\n{condensed_history}\n"
        )
