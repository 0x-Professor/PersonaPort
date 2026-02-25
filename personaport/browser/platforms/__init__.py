from __future__ import annotations

from personaport.browser.platforms.base import PlatformAdapter
from personaport.browser.platforms.chatgpt import ChatGPTAdapter
from personaport.browser.platforms.claude import ClaudeAdapter
from personaport.browser.platforms.gemini import GeminiAdapter
from personaport.models import Platform


def get_platform_adapter(platform: Platform | str) -> PlatformAdapter:
    platform_name = platform.value if isinstance(platform, Platform) else str(platform)
    mapping: dict[str, PlatformAdapter] = {
        Platform.CHATGPT.value: ChatGPTAdapter(),
        Platform.CLAUDE.value: ClaudeAdapter(),
        Platform.GEMINI.value: GeminiAdapter(),
    }
    if platform_name not in mapping:
        raise ValueError(f"Unsupported platform: {platform_name}")
    return mapping[platform_name]
