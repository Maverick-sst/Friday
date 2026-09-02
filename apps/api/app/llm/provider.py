"""LLM provider abstraction (PRD_3 §23 LLM Abstraction).

Agents depend only on this module's LLMProvider interface; swapping providers
is a config change, never an agent-code change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(slots=True)
class LLMResponse:
    text: str
    model_used: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}


class LLMProvider(ABC):
    """Provider-agnostic generation surface.

    Implementations MUST be safe for concurrent use and enforce timeouts,
    bounded retries with backoff, and model fallback chains internally.
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.4,
    ) -> LLMResponse:
        """Free-form generation."""

    @abstractmethod
    async def structured_generate(
        self,
        messages: list[Message],
        schema: type[BaseModel],
        *,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ) -> tuple[BaseModel, LLMResponse]:
        """Generation constrained to `schema`; validates and repairs once.

        Returns (validated_model_instance, raw_response).
        Raises OutputValidationError-equivalent (ValueError) after the
        repair attempt also fails.
        """
