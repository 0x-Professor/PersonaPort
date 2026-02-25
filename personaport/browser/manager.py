from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright


@dataclass
class BrowserRuntime:
    playwright: Playwright
    browser: Browser
    context: BrowserContext


class BrowserManager:
    def __init__(
        self,
        state_path: Path,
        *,
        headless: bool = False,
        slow_mo_ms: int = 50,
    ) -> None:
        self.state_path = state_path
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms

    @contextmanager
    def open(self) -> Iterator[BrowserRuntime]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless, slow_mo=self.slow_mo_ms
            )
            if self.state_path.exists():
                context = browser.new_context(
                    storage_state=str(self.state_path),
                    accept_downloads=True,
                )
            else:
                context = browser.new_context(accept_downloads=True)

            runtime = BrowserRuntime(playwright=playwright, browser=browser, context=context)
            try:
                yield runtime
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
