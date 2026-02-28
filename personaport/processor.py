from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterator

from rich.console import Console

from personaport.llm import (
    PROVIDERS,
    candidate_models,
    normalize_provider_name,
    provider_from_model,
    provider_key_env_vars,
    read_provider_key_from_env,
    resolve_provider_for_model,
)
from personaport.models import (
    ChatMessage,
    Conversation,
    MessageRole,
    PersonaProfile,
    Platform,
)

try:
    import litellm
    from litellm import completion
except Exception:  # pragma: no cover - defensive import fallback
    litellm = None
    completion = None

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}")
TASK_MARKERS = (
    "todo",
    "to do",
    "next step",
    "next steps",
    "need to",
    "must",
    "blocked",
    "stuck",
    "issue",
    "fix",
    "error",
    "deadline",
    "ship",
)
DECISION_MARKERS = (
    "decided",
    "we chose",
    "we will",
    "let's use",
    "selected",
    "finalize",
    "approved",
)
GOAL_MARKERS = (
    "build",
    "create",
    "implement",
    "migrate",
    "deploy",
    "design",
    "optimize",
)
STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "also",
    "and",
    "any",
    "are",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "could",
    "does",
    "doing",
    "down",
    "each",
    "from",
    "have",
    "having",
    "here",
    "into",
    "just",
    "like",
    "many",
    "more",
    "most",
    "other",
    "over",
    "same",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "very",
    "want",
    "with",
    "your",
    "you",
}
WORK_AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("api", "backend", "database", "sql", "fastapi", "django", "flask"),
    "frontend": ("react", "vue", "frontend", "ui", "css", "tailwind", "figma"),
    "devops": ("docker", "kubernetes", "ci", "deploy", "aws", "gcp", "azure"),
    "data-ai": ("llm", "model", "prompt", "embedding", "rag", "fine-tune", "agent"),
    "security": ("auth", "oauth", "security", "vulnerability", "encryption", "token"),
}


class ConversationProcessor:
    def __init__(
        self,
        *,
        console: Console,
        default_model: str = "ollama/llama3.1:8b",
        max_chunk_chars: int = 6000,
        max_context_chars: int = 22000,
        litellm_timeout_seconds: int = 120,
        provider_key_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self.console = console
        self.default_model = default_model
        self.max_chunk_chars = max_chunk_chars
        self.max_context_chars = max_context_chars
        self.litellm_timeout_seconds = litellm_timeout_seconds
        self.provider_key_lookup = provider_key_lookup
        self._llm_notice_printed = False
        if litellm is not None:
            # Prevent LiteLLM from printing repetitive provider-help blocks on handled failures.
            litellm.suppress_debug_info = True

    def load_conversations(
        self, file_path: Path, source_platform: Platform | str | None = None
    ) -> list[Conversation]:
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        platform = self._normalize_platform(source_platform)
        suffix = file_path.suffix.lower()

        if suffix in {".html", ".htm"}:
            html_text = file_path.read_text(encoding="utf-8", errors="ignore")
            return self._parse_chatgpt_html(html_text, source_name=file_path.name)

        if suffix == ".zip":
            return self._load_from_zip(file_path, platform)

        payload = self._load_json_payload(file_path)
        return self._parse_payload_by_platform(payload, platform)

    def combine_conversations(
        self, conversations: list[Conversation], source_platform: Platform | str
    ) -> Conversation:
        if not conversations:
            raise ValueError("Cannot combine zero conversations.")
        merged_messages: list[ChatMessage] = []
        for conversation in conversations:
            merged_messages.extend(conversation.messages)

        merged_messages.sort(
            key=lambda msg: msg.timestamp or datetime.fromtimestamp(0, tz=timezone.utc)
        )
        return Conversation(
            title=f"{source_platform} combined history",
            source_platform=source_platform,
            messages=merged_messages,
            metadata={"combined_conversations": [c.id for c in conversations]},
        )

    def extract_persona(
        self,
        conversations: list[Conversation],
        *,
        persona_override: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        api_keys: dict[str, str] | None = None,
        use_llm: bool = True,
        allow_remote_fallback: bool = True,
        remote_fallback_prompt: Callable[[str], bool] | None = None,
    ) -> PersonaProfile:
        if persona_override:
            return PersonaProfile(
                name="Manual Persona",
                system_prompt=persona_override.strip(),
                extracted_facts=[persona_override.strip()],
                style_notes=[],
                source_conversation_ids=[c.id for c in conversations],
            )

        history_excerpt = self._build_excerpt(conversations, max_chars=12000)
        if use_llm and completion is not None and history_excerpt:
            result = self._extract_persona_with_llm(
                history_excerpt,
                model=model,
                provider=provider,
                api_keys=api_keys,
                allow_remote_fallback=allow_remote_fallback,
                remote_fallback_prompt=remote_fallback_prompt,
            )
            if result is not None:
                result.source_conversation_ids = [c.id for c in conversations]
                return result

        return self._extract_persona_heuristic(conversations)

    def condense_history(
        self,
        conversation: Conversation,
        *,
        model: str | None = None,
        provider: str | None = None,
        api_keys: dict[str, str] | None = None,
        use_llm: bool = True,
        max_chars: int | None = None,
        allow_remote_fallback: bool = True,
        remote_fallback_prompt: Callable[[str], bool] | None = None,
    ) -> str:
        max_chars = max_chars or self.max_context_chars
        full_text = conversation.to_history_text()
        if len(full_text) <= max_chars:
            return full_text

        if use_llm and completion is not None:
            summary = self._summarize_with_llm(
                full_text,
                model=model,
                provider=provider,
                api_keys=api_keys,
                max_chars=max_chars,
                allow_remote_fallback=allow_remote_fallback,
                remote_fallback_prompt=remote_fallback_prompt,
            )
            if summary:
                return summary

        return self._heuristic_condensed_history(conversation, max_chars=max_chars)

    def _load_from_zip(self, zip_path: Path, platform: str) -> list[Conversation]:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(temp_root)

            json_files = sorted(temp_root.rglob("*.json"))
            html_files = sorted(temp_root.rglob("*.html"))

            prioritized_json = sorted(
                json_files,
                key=lambda path: (
                    0 if path.name.lower() == "conversations.json" else 1,
                    len(path.parts),
                ),
            )

            aggregated: list[Conversation] = []
            for json_path in prioritized_json:
                try:
                    payload = self._load_json_payload(json_path)
                except Exception:
                    continue
                parsed = self._parse_payload_by_platform(payload, platform)
                if parsed:
                    for conversation in parsed:
                        conversation.metadata.setdefault("raw_source_file", json_path.name)
                    aggregated.extend(parsed)

            deduped = self._dedupe_conversations(aggregated)
            if deduped:
                return deduped

            for html_path in html_files:
                try:
                    html_text = html_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                parsed = self._parse_chatgpt_html(html_text, source_name=html_path.name)
                if parsed:
                    return parsed

        raise ValueError(
            "ZIP export could not be parsed. Expected conversations JSON or ChatGPT chat.html."
        )

    def _load_json_payload(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _parse_payload_by_platform(self, payload: Any, platform: str) -> list[Conversation]:
        if platform == Platform.CHATGPT.value:
            conversations = self._parse_chatgpt(payload)
            if conversations:
                return conversations
            return self._parse_personaport_json(payload)

        if platform == Platform.CLAUDE.value:
            conversations = self._parse_claude(payload)
            if conversations:
                return conversations
            return self._parse_personaport_json(payload)

        if platform == Platform.GEMINI.value:
            conversations = self._parse_gemini(payload)
            if conversations:
                return conversations
            return self._parse_personaport_json(payload)

        # Unknown platform: best-effort parser chain.
        for parser in (
            self._parse_personaport_json,
            self._parse_chatgpt,
            self._parse_claude,
            self._parse_gemini,
        ):
            conversations = parser(payload)
            if conversations:
                return conversations
        return []

    def _parse_chatgpt_html(
        self, html_text: str, *, source_name: str = "chat.html"
    ) -> list[Conversation]:
        if not html_text.strip():
            return []

        article_blocks = re.findall(
            r"(?is)<article\b[^>]*>.*?</article>",
            html_text,
        )
        if not article_blocks:
            return []

        messages: list[ChatMessage] = []
        for index, block in enumerate(article_blocks):
            role = self._detect_chatgpt_html_role(block, index)
            text = self._strip_html(block)
            text = re.sub(r"^(You|ChatGPT|Assistant)\s+said:\s*", "", text, flags=re.IGNORECASE)
            text = text.strip()
            if not text:
                continue
            messages.append(
                ChatMessage(
                    role=role,
                    content=text,
                    timestamp=None,
                )
            )

        if not messages:
            return []

        title_match = re.search(r"(?is)<title>(.*?)</title>", html_text)
        raw_title = title_match.group(1).strip() if title_match else "ChatGPT Export"
        title = re.sub(r"\s+", " ", html_lib.unescape(raw_title))
        title = re.sub(r"^\s*ChatGPT\s*[-:]\s*", "", title, flags=re.IGNORECASE).strip()
        if not title:
            title = "ChatGPT Export"

        conv_hash = hashlib.sha1(html_text.encode("utf-8", errors="ignore")).hexdigest()[:16]
        conversation = Conversation(
            id=f"chatgpt_html_{conv_hash}",
            title=title,
            source_platform=Platform.CHATGPT,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            messages=messages,
            metadata={"raw_source": source_name, "format": "chatgpt_chat_html"},
        )
        return [conversation]

    def _detect_chatgpt_html_role(self, block: str, index: int) -> MessageRole:
        lowered = block.lower()
        if "you said:" in lowered:
            return MessageRole.USER
        if "chatgpt said:" in lowered or "assistant said:" in lowered:
            return MessageRole.ASSISTANT
        if 'data-message-author-role="user"' in lowered:
            return MessageRole.USER
        if 'data-message-author-role="assistant"' in lowered:
            return MessageRole.ASSISTANT
        # Fallback for ambiguous HTML exports: alternate roles by turn index.
        return MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT

    def _strip_html(self, value: str) -> str:
        without_scripts = re.sub(
            r"(?is)<(script|style)\b[^>]*>.*?</\1>",
            "",
            value,
        )
        without_tags = re.sub(r"(?is)<[^>]+>", "\n", without_scripts)
        normalized = html_lib.unescape(without_tags)
        normalized = re.sub(r"\r\n?", "\n", normalized)
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _parse_personaport_json(self, payload: Any) -> list[Conversation]:
        if isinstance(payload, list):
            conversations: list[Conversation] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                if {"id", "title", "messages"} <= set(item.keys()):
                    try:
                        conversations.append(Conversation.model_validate(item))
                    except Exception:
                        continue
            return conversations
        return []

    def _parse_chatgpt(self, payload: Any) -> list[Conversation]:
        if isinstance(payload, dict) and "conversations" in payload:
            payload = payload["conversations"]
        if not isinstance(payload, list):
            return []

        conversations: list[Conversation] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            mapping = item.get("mapping")
            if not isinstance(mapping, dict):
                continue

            messages: list[ChatMessage] = []
            for node in mapping.values():
                if not isinstance(node, dict):
                    continue
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                role = self._safe_role(
                    message.get("author", {}).get("role") or message.get("role")
                )
                if role is None:
                    continue
                content = self._extract_message_content(message)
                if not content:
                    continue
                timestamp = self._parse_timestamp(message.get("create_time"))
                messages.append(
                    ChatMessage(
                        role=role,
                        content=content,
                        timestamp=timestamp,
                    )
                )

            if not messages:
                continue
            messages.sort(
                key=lambda msg: msg.timestamp
                or datetime.fromtimestamp(0, tz=timezone.utc)
            )
            conversation = Conversation(
                id=str(item.get("id") or item.get("conversation_id") or ""),
                title=str(item.get("title") or "ChatGPT Conversation"),
                source_platform=Platform.CHATGPT,
                created_at=self._parse_timestamp(item.get("create_time"))
                or datetime.now(timezone.utc),
                updated_at=self._parse_timestamp(item.get("update_time"))
                or datetime.now(timezone.utc),
                messages=messages,
                metadata={"raw_source": "chatgpt_export"},
            )
            conversations.append(conversation)
        return conversations

    def _parse_claude(self, payload: Any) -> list[Conversation]:
        if isinstance(payload, dict):
            candidate = payload.get("conversations") or payload.get("chats")
            if isinstance(candidate, list):
                payload = candidate
        if not isinstance(payload, list):
            return []

        conversations: list[Conversation] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_messages = (
                item.get("chat_messages")
                or item.get("messages")
                or item.get("transcript")
                or []
            )
            if not isinstance(raw_messages, list):
                continue
            messages: list[ChatMessage] = []
            for entry in raw_messages:
                if not isinstance(entry, dict):
                    continue
                sender = (
                    entry.get("sender")
                    or entry.get("role")
                    or entry.get("author")
                    or ""
                )
                role = self._safe_role(sender)
                if role is None:
                    continue
                content = self._extract_text_fields(entry)
                if not content:
                    continue
                messages.append(
                    ChatMessage(
                        role=role,
                        content=content,
                        timestamp=self._parse_timestamp(
                            entry.get("created_at")
                            or entry.get("timestamp")
                            or entry.get("time")
                        ),
                    )
                )
            if not messages:
                continue
            messages.sort(
                key=lambda msg: msg.timestamp
                or datetime.fromtimestamp(0, tz=timezone.utc)
            )
            conversations.append(
                Conversation(
                    id=str(item.get("uuid") or item.get("id") or ""),
                    title=str(item.get("name") or item.get("title") or "Claude Conversation"),
                    source_platform=Platform.CLAUDE,
                    created_at=self._parse_timestamp(
                        item.get("created_at") or item.get("createdAt")
                    )
                    or datetime.now(timezone.utc),
                    updated_at=self._parse_timestamp(
                        item.get("updated_at") or item.get("updatedAt")
                    )
                    or datetime.now(timezone.utc),
                    messages=messages,
                    metadata={"raw_source": "claude_export"},
                )
            )
        return conversations

    def _parse_gemini(self, payload: Any) -> list[Conversation]:
        if isinstance(payload, dict):
            candidate = payload.get("conversations") or payload.get("threads")
            if isinstance(candidate, list):
                payload = candidate
        if not isinstance(payload, list):
            return []

        conversations: list[Conversation] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_messages = item.get("messages") or item.get("entries") or []
            if not isinstance(raw_messages, list):
                continue
            messages: list[ChatMessage] = []
            for entry in raw_messages:
                if not isinstance(entry, dict):
                    continue
                role = self._safe_role(entry.get("role") or entry.get("author"))
                if role is None:
                    continue
                content = self._extract_text_fields(entry)
                if not content:
                    continue
                messages.append(
                    ChatMessage(
                        role=role,
                        content=content,
                        timestamp=self._parse_timestamp(
                            entry.get("create_time")
                            or entry.get("created_at")
                            or entry.get("timestamp")
                        ),
                    )
                )
            if not messages:
                continue
            messages.sort(
                key=lambda msg: msg.timestamp
                or datetime.fromtimestamp(0, tz=timezone.utc)
            )
            conversations.append(
                Conversation(
                    id=str(item.get("id") or item.get("conversation_id") or ""),
                    title=str(item.get("title") or "Gemini Conversation"),
                    source_platform=Platform.GEMINI,
                    created_at=self._parse_timestamp(item.get("create_time"))
                    or datetime.now(timezone.utc),
                    updated_at=self._parse_timestamp(item.get("update_time"))
                    or datetime.now(timezone.utc),
                    messages=messages,
                    metadata={"raw_source": "gemini_export"},
                )
            )
        return conversations

    def _extract_persona_with_llm(
        self,
        history_excerpt: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        api_keys: dict[str, str] | None = None,
        allow_remote_fallback: bool = True,
        remote_fallback_prompt: Callable[[str], bool] | None = None,
    ) -> PersonaProfile | None:
        message_content = self._run_completion_with_fallback(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract persona from conversation history. "
                        "Return strict JSON with keys: system_prompt (string), "
                        "facts (array of strings), style_notes (array of strings)."
                    ),
                },
                {
                    "role": "user",
                    "content": history_excerpt,
                },
            ],
            temperature=0.1,
            model=model,
            provider=provider,
            api_keys=api_keys,
            purpose="persona extraction",
            allow_remote_fallback=allow_remote_fallback,
            remote_fallback_prompt=remote_fallback_prompt,
        )
        if not message_content:
            return None
        parsed = self._extract_json(message_content)
        if not parsed:
            return None
        return PersonaProfile(
            name="Extracted Persona",
            system_prompt=str(parsed.get("system_prompt", "")).strip(),
            extracted_facts=[
                str(value).strip() for value in parsed.get("facts", []) if str(value).strip()
            ],
            style_notes=[
                str(value).strip()
                for value in parsed.get("style_notes", [])
                if str(value).strip()
            ],
        )

    def _extract_persona_heuristic(
        self, conversations: list[Conversation]
    ) -> PersonaProfile:
        user_lines: list[str] = []
        for conversation in conversations:
            for message in conversation.messages:
                if message.role != MessageRole.USER:
                    continue
                text = message.content.strip()
                if text:
                    user_lines.append(text)

        preference_markers = [
            "i prefer",
            "i like",
            "i love",
            "i hate",
            "my project",
            "my style",
            "always",
            "never",
        ]
        facts: list[str] = []
        for line in user_lines[:200]:
            lower = line.lower()
            if any(marker in lower for marker in preference_markers):
                short = re.sub(r"\s+", " ", line).strip()
                facts.append(short[:220])
        facts = list(dict.fromkeys(facts))[:15]

        system_prompt = (
            "Use the user's prior context and preferences to answer consistently. "
            "Prioritize continuity with existing projects and communication style."
        )
        if facts:
            system_prompt = (
                "You are continuing an existing long-running assistant relationship. "
                "Honor these user preferences and constraints:\n- "
                + "\n- ".join(facts[:8])
            )

        return PersonaProfile(
            name="Heuristic Persona",
            system_prompt=system_prompt,
            extracted_facts=facts,
            style_notes=["Prefer concise, context-aware responses."],
            source_conversation_ids=[c.id for c in conversations],
        )

    def _summarize_with_llm(
        self,
        text: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        api_keys: dict[str, str] | None = None,
        max_chars: int,
        allow_remote_fallback: bool = True,
        remote_fallback_prompt: Callable[[str], bool] | None = None,
    ) -> str | None:
        chunks = self._chunk_text(text, chunk_size=self.max_chunk_chars)
        chunk_summaries: list[str] = []

        for chunk in chunks:
            summary = self._run_completion_with_fallback(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the chat segment preserving user goals, "
                            "open tasks, constraints, technical decisions, and preferences."
                        ),
                    },
                    {"role": "user", "content": chunk},
                ],
                temperature=0.2,
                model=model,
                provider=provider,
                api_keys=api_keys,
                purpose="history summarization",
                allow_remote_fallback=allow_remote_fallback,
                remote_fallback_prompt=remote_fallback_prompt,
            )
            if not summary:
                return None
            chunk_summaries.append(summary.strip())

        merged = "\n\n".join(chunk_summaries)
        if len(merged) > max_chars:
            return merged[: max_chars - 3] + "..."
        return merged

    def build_context_map(
        self,
        conversations: list[Conversation],
        *,
        max_topics: int = 20,
        max_threads: int = 8,
        max_items: int = 15,
    ) -> dict[str, Any]:
        topic_counter: Counter[str] = Counter()
        work_area_counter: Counter[str] = Counter()
        open_tasks: list[str] = []
        decisions: list[str] = []
        goals: list[str] = []
        thread_rankings: list[dict[str, Any]] = []
        total_messages = 0
        total_user_messages = 0

        now = datetime.now(timezone.utc)
        for conversation in conversations:
            total_messages += len(conversation.messages)
            task_hits = 0
            decision_hits = 0
            goal_hits = 0

            for message in conversation.messages:
                normalized = self._normalize_line(message.content)
                if not normalized:
                    continue

                lowered = normalized.lower()
                if message.role == MessageRole.USER:
                    total_user_messages += 1
                    topic_counter.update(self._extract_keywords(normalized))
                    work_area_counter.update(self._infer_work_areas(lowered))

                    if self._contains_any(lowered, TASK_MARKERS):
                        open_tasks.append(normalized)
                        task_hits += 1
                    if self._contains_any(lowered, DECISION_MARKERS):
                        decisions.append(normalized)
                        decision_hits += 1
                    if self._contains_any(lowered, GOAL_MARKERS):
                        goals.append(normalized)
                        goal_hits += 1

            age_days = max(0, (now - conversation.updated_at).days)
            recency_score = max(1, 30 - age_days)
            thread_score = (
                min(len(conversation.messages), 60)
                + (task_hits * 4)
                + (decision_hits * 3)
                + (goal_hits * 2)
                + recency_score
            )
            thread_rankings.append(
                {
                    "id": conversation.id,
                    "title": conversation.title,
                    "updated_at": conversation.updated_at.isoformat(),
                    "message_count": len(conversation.messages),
                    "score": thread_score,
                    "focus": self._thread_focus(conversation),
                }
            )

        sorted_threads = sorted(
            thread_rankings,
            key=lambda item: (
                item["score"],
                item["updated_at"],
                item["message_count"],
            ),
            reverse=True,
        )[:max_threads]

        top_topics = [
            {"topic": topic, "frequency": freq}
            for topic, freq in topic_counter.most_common(max_topics)
            if freq > 0
        ]
        work_profile = [
            {"area": area, "frequency": freq}
            for area, freq in work_area_counter.most_common()
            if freq > 0
        ]

        return {
            "stats": {
                "conversation_count": len(conversations),
                "message_count": total_messages,
                "user_message_count": total_user_messages,
            },
            "top_topics": top_topics,
            "work_profile": work_profile,
            "priority_threads": sorted_threads,
            "open_tasks": self._dedupe_lines(open_tasks, max_items=max_items),
            "decisions": self._dedupe_lines(decisions, max_items=max_items),
            "goals": self._dedupe_lines(goals, max_items=max_items),
        }

    def _heuristic_condensed_history(self, conversation: Conversation, *, max_chars: int) -> str:
        recent_turns = conversation.messages[-12:]
        recent_lines = [
            f"- {message.role.value}: {self._normalize_line(message.content, max_len=220)}"
            for message in recent_turns
            if self._normalize_line(message.content, max_len=220)
        ]
        open_tasks: list[str] = []
        decisions: list[str] = []
        for message in conversation.messages:
            if message.role != MessageRole.USER:
                continue
            normalized = self._normalize_line(message.content)
            if not normalized:
                continue
            lowered = normalized.lower()
            if self._contains_any(lowered, TASK_MARKERS):
                open_tasks.append(normalized)
            if self._contains_any(lowered, DECISION_MARKERS):
                decisions.append(normalized)

        parts: list[str] = [
            f"Conversation: {conversation.title}",
            "",
            "Active tasks:",
        ]
        task_lines = self._dedupe_lines(open_tasks, max_items=8)
        if task_lines:
            parts.extend(f"- {line}" for line in task_lines)
        else:
            parts.append("- No explicit tasks extracted. Continue from recent turns.")

        parts.append("")
        parts.append("Key decisions:")
        decision_lines = self._dedupe_lines(decisions, max_items=6)
        if decision_lines:
            parts.extend(f"- {line}" for line in decision_lines)
        else:
            parts.append("- No explicit decisions extracted.")

        parts.append("")
        parts.append("Recent turns:")
        parts.extend(recent_lines)

        condensed = "\n".join(parts).strip()
        if len(condensed) > max_chars:
            return condensed[: max_chars - 3] + "..."
        return condensed

    def _run_completion_with_fallback(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        model: str | None,
        provider: str | None,
        api_keys: dict[str, str] | None,
        purpose: str,
        allow_remote_fallback: bool = True,
        remote_fallback_prompt: Callable[[str], bool] | None = None,
    ) -> str | None:
        if completion is None:
            return None

        selected_provider = normalize_provider_name(provider, allow_custom=True)
        explicit_remote_selection = self._has_explicit_remote_selection(
            selected_provider=selected_provider,
            selected_model=model,
        )
        remote_fallback_allowed = allow_remote_fallback
        models = candidate_models(
            selected_model=model,
            selected_provider=selected_provider,
            default_model=self.default_model,
        )
        attempt_count = 0
        had_runtime_error = False
        skipped_remote = False
        for candidate in models:
            provider_for_model = resolve_provider_for_model(candidate, selected_provider)
            if self._is_remote_provider(provider_for_model):
                if not remote_fallback_allowed:
                    skipped_remote = True
                    continue
                if not explicit_remote_selection and remote_fallback_prompt is not None:
                    remote_fallback_allowed = bool(remote_fallback_prompt(candidate))
                    if not remote_fallback_allowed:
                        skipped_remote = True
                        continue

            api_key = self._resolve_provider_key(provider_for_model, api_keys)
            if self._provider_requires_key(provider_for_model) and not api_key:
                continue

            try:
                with self._provider_env(provider_for_model, api_key):
                    attempt_count += 1
                    response = completion(
                        model=candidate,
                        messages=messages,
                        temperature=temperature,
                        timeout=self.litellm_timeout_seconds,
                    )
                content = response["choices"][0]["message"]["content"]
                if isinstance(content, str) and content.strip():
                    return content.strip()
            except Exception:
                had_runtime_error = True
                continue

        if attempt_count == 0:
            if skipped_remote:
                self._notify_llm_fallback_once(
                    f"LiteLLM {purpose} stayed in local mode; remote fallback is disabled. Using heuristics."
                )
                return None
            self._notify_llm_fallback_once(
                f"LiteLLM {purpose} skipped: no reachable configured provider/model. Using heuristics."
            )
            return None
        if had_runtime_error:
            if skipped_remote and not remote_fallback_allowed:
                self._notify_llm_fallback_once(
                    (
                        f"LiteLLM {purpose} unavailable in local mode, and remote fallback is "
                        "disabled. Using heuristics."
                    )
                )
                return None
            self._notify_llm_fallback_once(
                (
                    f"LiteLLM {purpose} unavailable (provider/network/rate-limit). "
                    "Using heuristics."
                )
            )
        return None

    def _is_remote_provider(self, provider: str | None) -> bool:
        if not provider:
            return False
        return provider != "ollama"

    def _has_explicit_remote_selection(
        self,
        *,
        selected_provider: str | None,
        selected_model: str | None,
    ) -> bool:
        if selected_provider and selected_provider != "ollama":
            return True
        model_provider = provider_from_model(selected_model)
        return bool(model_provider and model_provider != "ollama")

    def _resolve_provider_key(
        self,
        provider: str | None,
        api_keys: dict[str, str] | None,
    ) -> str | None:
        if not provider:
            return None
        if api_keys and api_keys.get(provider):
            return api_keys[provider]
        from_env = read_provider_key_from_env(provider)
        if from_env:
            return from_env
        if self.provider_key_lookup:
            return self.provider_key_lookup(provider)
        return None

    def _provider_requires_key(self, provider: str | None) -> bool:
        if not provider:
            return False
        spec = PROVIDERS.get(provider)
        if spec is not None:
            return spec.requires_api_key
        return False

    @contextmanager
    def _provider_env(self, provider: str | None, api_key: str | None) -> Iterator[None]:
        if not provider or not api_key:
            yield
            return

        env_vars = provider_key_env_vars(provider)
        if not env_vars:
            yield
            return
        original_values = {env_var: os.getenv(env_var) for env_var in env_vars}
        try:
            for env_var in env_vars:
                os.environ[env_var] = api_key
            yield
        finally:
            for env_var, original in original_values.items():
                if original is None:
                    os.environ.pop(env_var, None)
                else:
                    os.environ[env_var] = original

    def _notify_llm_fallback_once(self, message: str) -> None:
        if self._llm_notice_printed:
            return
        self.console.print(f"[yellow]{message}[/yellow]")
        self._llm_notice_printed = True

    def _build_excerpt(self, conversations: list[Conversation], max_chars: int) -> str:
        combined: list[str] = []
        total = 0
        for conversation in conversations:
            chunk = f"Conversation: {conversation.title}\n{conversation.to_history_text()}\n\n"
            if total + len(chunk) > max_chars:
                break
            combined.append(chunk)
            total += len(chunk)
        return "".join(combined).strip()

    def _normalize_line(self, text: str, *, max_len: int = 280) -> str:
        normalized = re.sub(r"\s+", " ", str(text)).strip()
        if len(normalized) > max_len:
            return normalized[: max_len - 3] + "..."
        return normalized

    def _contains_any(self, text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)

    def _extract_keywords(self, text: str) -> list[str]:
        keywords: list[str] = []
        for token in TOKEN_PATTERN.findall(text.lower()):
            if token in STOPWORDS:
                continue
            if token.isdigit():
                continue
            keywords.append(token)
        return keywords

    def _infer_work_areas(self, lowered_text: str) -> list[str]:
        areas: list[str] = []
        for area, markers in WORK_AREA_KEYWORDS.items():
            if any(marker in lowered_text for marker in markers):
                areas.append(area)
        return areas

    def _thread_focus(self, conversation: Conversation) -> str:
        recent_user = [
            self._normalize_line(message.content, max_len=160)
            for message in reversed(conversation.messages)
            if message.role == MessageRole.USER and self._normalize_line(message.content, max_len=160)
        ]
        if not recent_user:
            return "General conversation context."
        return " | ".join(reversed(recent_user[:2]))

    def _dedupe_lines(self, lines: list[str], *, max_items: int) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = self._normalize_line(line)
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            deduped.append(normalized)
            seen.add(lowered)
            if len(deduped) >= max_items:
                break
        return deduped

    def _normalize_platform(self, platform: Platform | str | None) -> str:
        if platform is None:
            return Platform.GENERIC.value
        if isinstance(platform, Platform):
            return platform.value
        return str(platform).strip().lower()

    def _safe_role(self, raw_role: Any) -> MessageRole | None:
        role = str(raw_role or "").lower().strip()
        mapping = {
            "user": MessageRole.USER,
            "human": MessageRole.USER,
            "assistant": MessageRole.ASSISTANT,
            "model": MessageRole.ASSISTANT,
            "system": MessageRole.SYSTEM,
        }
        return mapping.get(role)

    def _extract_message_content(self, message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                normalized = [self._extract_part_text(part) for part in parts]
                normalized = [part for part in normalized if part]
                if normalized:
                    return "\n".join(normalized).strip()
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
        if isinstance(content, str):
            return content.strip()
        return self._extract_text_fields(message)

    def _extract_text_fields(self, item: dict[str, Any]) -> str:
        keys = ["text", "content", "body", "message"]
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                texts: list[str] = []
                for entry in value:
                    extracted = self._extract_part_text(entry)
                    if extracted:
                        texts.append(extracted)
                if texts:
                    return "\n".join(texts)
            if isinstance(value, dict):
                nested_extracted = self._extract_part_text(value)
                if nested_extracted:
                    return nested_extracted
        attachment_text = self._extract_attachment_text(item)
        if attachment_text:
            return attachment_text
        return ""

    def _extract_part_text(self, value: Any) -> str:
        if isinstance(value, str):
            text = value.strip()
            return text if text else ""

        if not isinstance(value, dict):
            return ""

        # Claude structured content items.
        item_type = str(value.get("type") or "").lower().strip()
        if item_type == "thinking":
            summaries = value.get("summaries")
            if isinstance(summaries, list):
                summary_texts = [
                    str(summary.get("summary")).strip()
                    for summary in summaries
                    if isinstance(summary, dict) and str(summary.get("summary", "")).strip()
                ]
                if summary_texts:
                    return " ".join(summary_texts)
            return ""

        for key in ("text", "content", "summary"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        # ChatGPT multimodal asset placeholders.
        content_type = str(value.get("content_type") or "").lower().strip()
        if content_type == "image_asset_pointer":
            pointer = value.get("asset_pointer")
            if isinstance(pointer, str) and pointer.strip():
                return f"[Image attachment: {pointer}]"
            return "[Image attachment]"

        if content_type in {"audio_asset_pointer", "video_asset_pointer"}:
            pointer = value.get("asset_pointer")
            if isinstance(pointer, str) and pointer.strip():
                return f"[{content_type}: {pointer}]"
            return f"[{content_type}]"

        return ""

    def _extract_attachment_text(self, item: dict[str, Any]) -> str:
        collected: list[str] = []
        attachments = item.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                extracted = attachment.get("extracted_content")
                if isinstance(extracted, str) and extracted.strip():
                    collected.append(extracted.strip())
                    continue
                file_name = attachment.get("file_name")
                if isinstance(file_name, str) and file_name.strip():
                    collected.append(f"[Attachment: {file_name.strip()}]")

        files = item.get("files")
        if isinstance(files, list):
            for file_item in files:
                if not isinstance(file_item, dict):
                    continue
                file_name = file_item.get("file_name")
                if isinstance(file_name, str) and file_name.strip():
                    collected.append(f"[File: {file_name.strip()}]")

        return "\n".join(collected).strip()

    def _dedupe_conversations(self, conversations: list[Conversation]) -> list[Conversation]:
        deduped: dict[str, Conversation] = {}
        for conversation in conversations:
            if conversation.id:
                dedupe_key = conversation.id
            else:
                dedupe_key = hashlib.sha1(
                    f"{conversation.title}|{conversation.created_at.isoformat()}".encode("utf-8")
                ).hexdigest()
            existing = deduped.get(dedupe_key)
            if existing is None or len(conversation.messages) > len(existing.messages):
                deduped[dedupe_key] = conversation
        return sorted(deduped.values(), key=lambda conv: conv.updated_at, reverse=True)

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (ValueError, OSError):
                return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if value.isdigit():
                try:
                    return datetime.fromtimestamp(float(value), tz=timezone.utc)
                except (ValueError, OSError):
                    return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _chunk_text(self, text: str, *, chunk_size: int) -> list[str]:
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            chunks.append(text[start : start + chunk_size])
            start += chunk_size
        return chunks

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
        return None
