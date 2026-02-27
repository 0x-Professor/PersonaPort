from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page
from rich.console import Console

from personaport.browser.platforms.base import ExportResult, PlatformAdapter
from personaport.models import Conversation, Platform


class GeminiAdapter(PlatformAdapter):
    platform = Platform.GEMINI
    home_url = "https://gemini.google.com/app"
    new_chat_url = "https://gemini.google.com/app"

    def login(self, page: Page, console: Console) -> None:
        page.goto(self.home_url, wait_until="domcontentloaded")
        console.print(
            "Complete Gemini/Google login manually in the browser window, then return to terminal."
        )

    def export_data(
        self,
        page: Page,
        exports_dir: Path,
        *,
        safe_mode: bool,
        no_scrape: bool,
        console: Console,
    ) -> ExportResult:
        page.goto(self.home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        console.print(
            "Gemini export automation is conservative in v0.1. "
            "Use official Google export/download options when available."
        )
        if safe_mode or no_scrape:
            return ExportResult(
                status="manual_required",
                message=(
                    "Run Google/Gemini official export flow manually, then process the file with "
                    "`personaport process --file <export>`."
                ),
            )

        conversations = self._scrape_conversations(page, console)
        if not conversations:
            return ExportResult(
                status="failed",
                message="No conversations scraped from Gemini.",
            )
        export_path = self._write_scraped_export(conversations, exports_dir)
        return ExportResult(
            status="success",
            message=f"Scraped {len(conversations)} conversations from Gemini.",
            export_path=export_path,
            conversations=conversations,
        )

    def inject_payload(
        self,
        page: Page,
        prompt_text: str,
        knowledge_files: list[Path] | None,
        console: Console,
    ) -> None:
        page.goto(self.new_chat_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        if knowledge_files:
            uploaded = self._try_file_upload(page, knowledge_files)
            if uploaded:
                files = ", ".join(str(path) for path in knowledge_files)
                console.print(f"[green]Uploaded knowledge file(s):[/green] {files}")

        if not self._fill_prompt_box(page, prompt_text):
            raise RuntimeError("Unable to find Gemini input box to inject migration prompt.")

        page.keyboard.press("Enter")
        console.print("[green]Migration prompt injected into Gemini.[/green]")

    def _scrape_conversations(self, page: Page, console: Console) -> list[Conversation]:
        page.goto(self.home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        links = page.eval_on_selector_all(
            "a[href*='/app/']",
            "els => [...new Set(els.map(e => e.href))]",
        )
        if not links:
            links = [page.url]

        conversations: list[Conversation] = []
        for raw_link in links[:20]:
            page.goto(raw_link, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            messages = self._extract_dom_messages(page)
            if not messages:
                continue
            conv_id = raw_link.rstrip("/").split("/")[-1] or "gemini-current"
            conversations.append(
                Conversation(
                    id=conv_id,
                    title=page.title() or f"Gemini Chat {conv_id}",
                    source_platform=self.platform,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    messages=messages,
                    metadata={"url": raw_link, "scraped": True},
                )
            )

        console.print(f"Scraped {len(conversations)} Gemini conversation(s).")
        return conversations

    def _fill_prompt_box(self, page: Page, prompt_text: str) -> bool:
        selectors = [
            "textarea",
            "div[contenteditable='true']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.click(timeout=1000)
                if selector == "textarea":
                    locator.fill(prompt_text, timeout=1000)
                else:
                    page.keyboard.type(prompt_text, delay=0)
                return True
            except Exception:
                continue
        return False

    def _try_file_upload(self, page: Page, paths: list[Path]) -> bool:
        selectors = [
            "input[type='file']",
            "input[accept*='text']",
        ]
        normalized = [str(path) for path in paths]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.set_input_files(normalized, timeout=2000)
                return True
            except Exception:
                continue
        return False
