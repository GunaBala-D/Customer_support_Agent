from app.services.llm.base import LLMError, LLMProvider, LLMResult


def get_llm(settings) -> LLMProvider:
    if settings.llm_provider == "ollama":
        from app.services.llm.ollama import OllamaProvider

        return OllamaProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            num_ctx=settings.ollama_num_ctx,
            timeout=settings.ollama_timeout_seconds,
            default_temperature=settings.llm_temperature,
            default_max_tokens=settings.llm_max_tokens,
        )
    if settings.llm_provider == "openai_compatible":
        from app.services.llm.openai_compat import OpenAICompatProvider

        if not settings.openai_base_url or not settings.openai_model:
            raise ValueError("openai_compatible needs a base URL and a model name")
        return OpenAICompatProvider(
            settings.openai_base_url, settings.openai_model, settings.openai_api_key,
            timeout=settings.ollama_timeout_seconds, default_temperature=settings.llm_temperature,
            default_max_tokens=settings.llm_max_tokens,
        )
    if settings.llm_provider == "extractive":
        from app.services.llm.extractive import ExtractiveProvider

        return ExtractiveProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


__all__ = ["LLMError", "LLMProvider", "LLMResult", "get_llm"]
