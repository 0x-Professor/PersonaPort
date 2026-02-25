from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from rich.console import Console

from personaport.models import ChatMessage, Conversation, MessageRole, Platform


@dataclass
class ExportResult:
    status: str
    message: str
    export_path: Path | None = None
    conversations: list[Conversation] = field(default_factory=list)


class PlatformAdapter(ABC):
    platform: Platform
    home_url: str
    new_chat_url: str

    @abstractmethod
    def login(self, page: Page, console: Console) -> None:
        raise NotImplementedError

    @abstractmethod
    def export_data(
        self,
        page: Page,
        exports_dir: Path,
        *,
        safe_mode: bool,
        no_scrape: bool,
        console: Console,
    ) -> ExportResult:
        raise NotImplementedError

    @abstractmethod
    def inject_payload(
        self,
        page: Page,
        prompt_text: str,
        knowledge_file: Path | None,
        console: Console,
    ) -> None:
        raise NotImplementedError

    def _click_first(self, page: Page, selectors: list[str], timeout_ms: int = 1500) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=timeout_ms)
                locator.click(timeout=timeout_ms)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _write_scraped_export(
        self, conversations: list[Conversation], exports_dir: Path
    ) -> Path:
        exports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = exports_dir / f"{self.platform.value}_scraped_{timestamp}.json"
        payload = [conversation.model_dump(mode="json") for conversation in conversations]
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return output_path

    def _extract_dom_messages(
        self, page: Page, *, role_attr: str = "data-message-author-role"
    ) -> list[ChatMessage]:
        raw_messages = page.evaluate(
            """
            (roleAttr) => {
              const selectors = [
                `[${roleAttr}]`,
                "[data-testid='user-message']",
                "[data-testid='assistant-message']"
              ];
              const merged = [];
              selectors.forEach((selector) => {
                document.querySelectorAll(selector).forEach((node) => merged.push(node));
              });
              const unique = Array.from(new Set(merged));
              return unique.map((node) => {
                let role = node.getAttribute(roleAttr) || "";
                const testId = node.getAttribute("data-testid") || "";
                if (!role && testId.includes("user")) role = "user";
                if (!role && testId.includes("assistant")) role = "assistant";
                return {
                  role,
                  content: (node.innerText || "").trim()
                };
              }).filter((item) => item.content.length > 0);
            }
            """,
            role_attr,
        )
        messages: list[ChatMessage] = []
        for item in raw_messages:
            role = str(item.get("role", "")).lower()
            if role not in {MessageRole.USER.value, MessageRole.ASSISTANT.value}:
                role = MessageRole.USER.value if not messages else MessageRole.ASSISTANT.value
            messages.append(
                ChatMessage(
                    role=MessageRole(role),
                    content=str(item.get("content", "")).strip(),
                    timestamp=datetime.now(timezone.utc),
                )
            )
        return messages
