from __future__ import annotations

import os
import re
from dataclasses import dataclass

PROVIDER_SECRET_PREFIX = "llm_api_key"
CUSTOM_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    default_model: str
    key_env_vars: tuple[str, ...] = ()
    free_tier: bool = False
    notes: str = ""

    @property
    def requires_api_key(self) -> bool:
        return len(self.key_env_vars) > 0


PROVIDERS: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        name="ollama",
        display_name="Ollama (Local)",
        default_model="ollama/llama3.1:8b",
        free_tier=True,
        notes="Runs local models without cloud API keys.",
    ),
    "groq": ProviderSpec(
        name="groq",
        display_name="Groq",
        default_model="groq/llama-3.1-8b-instant",
        key_env_vars=("GROQ_API_KEY",),
        notes="Fast hosted inference for Llama, Mixtral, and other hosted models.",
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        display_name="OpenRouter",
        default_model="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        key_env_vars=("OPENROUTER_API_KEY",),
        notes="Single endpoint for many providers/models.",
    ),
    "openai": ProviderSpec(
        name="openai",
        display_name="OpenAI",
        default_model="gpt-4o-mini",
        key_env_vars=("OPENAI_API_KEY",),
    ),
    "anthropic": ProviderSpec(
        name="anthropic",
        display_name="Anthropic",
        default_model="anthropic/claude-3-5-haiku-latest",
        key_env_vars=("ANTHROPIC_API_KEY",),
    ),
    "gemini": ProviderSpec(
        name="gemini",
        display_name="Google Gemini",
        default_model="gemini/gemini-1.5-flash",
        key_env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
    "together": ProviderSpec(
        name="together",
        display_name="Together AI",
        default_model="together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        key_env_vars=("TOGETHER_API_KEY", "TOGETHERAI_API_KEY"),
        notes="Supports open-source model endpoints.",
    ),
}

FALLBACK_MODEL_CHAIN: tuple[str, ...] = (
    "ollama/llama3.1:8b",
    "groq/llama-3.1-8b-instant",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
)


def normalize_provider_name(provider: str | None, *, allow_custom: bool = False) -> str | None:
    if provider is None:
        return None
    value = provider.strip().lower()
    if not value:
        return None
    if value not in PROVIDERS:
        if allow_custom and CUSTOM_PROVIDER_RE.match(value):
            return value
        return None
    return value


def provider_secret_name(provider: str) -> str:
    return f"{PROVIDER_SECRET_PREFIX}:{provider}"


def provider_from_model(model: str | None) -> str | None:
    if not model:
        return None
    lowered = model.lower().strip()
    if "/" in lowered:
        prefix = lowered.split("/", 1)[0]
        if prefix:
            return prefix
    return None


def candidate_models(
    *,
    selected_model: str | None,
    selected_provider: str | None,
    default_model: str,
) -> list[str]:
    ordered: list[str] = []

    def add(model: str | None) -> None:
        if not model:
            return
        normalized = model.strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    if selected_model:
        stripped_selected = selected_model.strip()
        if selected_provider and not stripped_selected.lower().startswith(f"{selected_provider}/"):
            add(f"{selected_provider}/{stripped_selected}")
        add(stripped_selected)

    if selected_provider and selected_provider in PROVIDERS:
        add(PROVIDERS[selected_provider].default_model)

    add(default_model)
    for model in FALLBACK_MODEL_CHAIN:
        add(model)
    return ordered


def resolve_provider_for_model(model: str, selected_provider: str | None) -> str | None:
    explicit = provider_from_model(model)
    if explicit:
        return explicit
    if selected_provider:
        return selected_provider
    return None


def read_provider_key_from_env(provider: str) -> str | None:
    for env_var in provider_key_env_vars(provider):
        value = os.getenv(env_var)
        if value:
            return value
    return None


def provider_key_env_vars(provider: str) -> tuple[str, ...]:
    spec = PROVIDERS.get(provider)
    if spec is not None:
        return spec.key_env_vars
    normalized = provider.strip().upper().replace("-", "_")
    if not normalized:
        return tuple()
    return (f"{normalized}_API_KEY",)
