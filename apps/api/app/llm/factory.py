"""LLM provider factory.

Returns a process-wide provider instance. Agents never import provider
modules directly; they ask this factory for `LLMProvider`.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.llm.provider import LLMProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if not settings.llm_configured:
        raise RuntimeError(
            "LLM not configured: set STRATEGY_LLM_BASE_URL / STRATEGY_LLM_API_KEY / STRATEGY_LLM_MODEL"
        )
    from app.llm.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider()
