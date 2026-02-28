from __future__ import annotations

from personaport.llm import candidate_models, normalize_provider_name, provider_from_model


def test_normalize_provider_name() -> None:
    assert normalize_provider_name("groq") == "groq"
    assert normalize_provider_name("GROQ") == "groq"
    assert normalize_provider_name("unknown") is None


def test_provider_from_model_detects_prefix() -> None:
    assert provider_from_model("openrouter/meta-llama/llama-3.3-70b-instruct:free") == "openrouter"
    assert provider_from_model("gpt-4o-mini") is None


def test_candidate_models_includes_provider_default_and_fallbacks() -> None:
    models = candidate_models(
        selected_model="openai/gpt-oss-20b",
        selected_provider="groq",
        default_model="ollama/llama3.1:8b",
    )

    assert "openai/gpt-oss-20b" in models
    assert "groq/openai/gpt-oss-20b" in models
    assert models[0] == "groq/openai/gpt-oss-20b"
