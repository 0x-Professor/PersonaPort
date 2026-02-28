from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

CHROMIUM_INSTALL_CMD = "python -m playwright install chromium"


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
        downloads_path: Path | None = None,
        headless: bool = False,
        slow_mo_ms: int = 50,
    ) -> None:
        self.state_path = state_path
        self.downloads_path = downloads_path
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms

    @contextmanager
    def open(self) -> Iterator[BrowserRuntime]:
        with sync_playwright() as playwright:
            if self.downloads_path is not None:
                self.downloads_path.mkdir(parents=True, exist_ok=True)
            browser = playwright.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                downloads_path=str(self.downloads_path) if self.downloads_path else None,
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


def chromium_executable_status() -> tuple[bool, Path | None]:
    try:
        with sync_playwright() as playwright:
            executable_path = Path(playwright.chromium.executable_path)
    except Exception:
        return False, None
    return executable_path.exists(), executable_path


def harden_session_state_permissions(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        if os.name == "nt":
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        else:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return True
    except OSError:
        return False
