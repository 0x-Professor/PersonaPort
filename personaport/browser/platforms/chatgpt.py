from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page
from rich.console import Console

from personaport.browser.platforms.base import ExportResult, PlatformAdapter
from personaport.models import Conversation, Platform


class ChatGPTAdapter(PlatformAdapter):
    platform = Platform.CHATGPT
    home_url = "https://chatgpt.com/"
    new_chat_url = "https://chatgpt.com/"

    def login(self, page: Page, console: Console) -> None:
        page.goto(self.home_url, wait_until="domcontentloaded")
        console.print(
            "Complete ChatGPT login manually in the browser window, then return to terminal."
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

        clicked_export = self._click_first(
            page,
            [
                "button[aria-label*='Settings']",
                "button:has-text('Settings')",
                "button:has-text('Data controls')",
                "button:has-text('Export data')",
            ],
        )
        if clicked_export:
            console.print(
                "[green]Attempted official export flow.[/green] "
                "ChatGPT usually delivers export links by email."
            )

        if safe_mode or no_scrape:
            return ExportResult(
                status="manual_required",
                message=(
                    "Official export requested. Complete export manually if prompted and "
                    "then run `personaport process --file <export.zip>`."
                ),
            )

        conversations = self._scrape_conversations(page, console)
        if not conversations:
            return ExportResult(
                status="failed",
                message="No conversations scraped. Try safe mode with manual export.",
            )

        export_path = self._write_scraped_export(conversations, exports_dir)
        return ExportResult(
            status="success",
            message=f"Scraped {len(conversations)} conversations from ChatGPT.",
            export_path=export_path,
            conversations=conversations,
        )

    def inject_payload(
        self,
        page: Page,
        prompt_text: str,
        knowledge_file: Path | None,
        console: Console,
    ) -> None:
        page.goto(self.new_chat_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        if knowledge_file:
            upload_success = self._try_file_upload(page, knowledge_file)
            if upload_success:
                console.print(f"[green]Uploaded knowledge file:[/green] {knowledge_file}")

        if not self._fill_prompt_box(page, prompt_text):
            raise RuntimeError("Unable to find ChatGPT input box to inject migration prompt.")

        page.keyboard.press("Enter")
        console.print("[green]Migration prompt injected into ChatGPT.[/green]")

    def _scrape_conversations(self, page: Page, console: Console) -> list[Conversation]:
        page.goto(self.home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        links = page.eval_on_selector_all(
            "a[href*='/c/']",
            "els => [...new Set(els.map(e => e.href))]",
        )
        if not links and "/c/" in page.url:
            links = [page.url]

        conversations: list[Conversation] = []
        for raw_link in links[:20]:
            page.goto(raw_link, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            messages = self._extract_dom_messages(page)
            if not messages:
                continue
            conv_id = raw_link.rstrip("/").split("/")[-1]
            title = page.title() or f"ChatGPT Conversation {conv_id}"
            conversations.append(
                Conversation(
                    id=conv_id,
                    title=title,
                    source_platform=self.platform,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    messages=messages,
                    metadata={"url": raw_link, "scraped": True},
                )
            )

        console.print(f"Scraped {len(conversations)} ChatGPT conversation(s).")
        return conversations

    def _fill_prompt_box(self, page: Page, prompt_text: str) -> bool:
        selectors = [
            "#prompt-textarea",
            "textarea[data-id='root']",
            "textarea",
            "[contenteditable='true']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.click(timeout=1000)
                if selector == "[contenteditable='true']":
                    page.keyboard.type(prompt_text, delay=0)
                else:
                    locator.fill(prompt_text, timeout=1000)
                return True
            except Exception:
                continue
        return False

    def _try_file_upload(self, page: Page, path: Path) -> bool:
        selectors = [
            "input[type='file']",
            "input[accept*='text']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.set_input_files(str(path), timeout=1000)
                return True
            except Exception:
                continue
        return False
