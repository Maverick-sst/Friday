"""OpenAI-compatible tool-calling brain.

Activates when AGENT_LLM_* - or, failing that, the STRATEGY_LLM_* provider - is
configured. The model sees the six gateway tools and the user's intent/
constraints; it can propose actions, but amounts, authorization, and execution
stay server-side.

Resilience matches the strategy fleet (app.llm.openai_compat): transient HTTP
statuses and transport errors are retried with backoff, a model that returns a
200-without-valid-choices envelope (OpenRouter upstream failure) is skipped in
favor of the next model in the chain, and a dead chain raises a descriptive
error instead of a parser crash aborting the whole transaction flow.
"""

import json
import logging
import time

import httpx

from app.agent.tools import build_tools
from app.core.config import get_settings

logger = logging.getLogger("acg.llm_brain")

SYSTEM_PROMPT = """You are a careful autonomous shopping agent buying on behalf of a user.

Rules you must follow:
- Use ONLY the provided tools. You cannot browse, call APIs, or move money yourself.
- Discover the merchant first, then search, then quote the best matching available variant.
- Respect the user's constraints (budget, size, preferences) when choosing.
- Prices/availability come only from tool results; never invent or modify amounts.
- After quoting, create a cart and attempt checkout once.
- If checkout is blocked, report the reason faithfully - do not retry with altered data.

Finish as soon as checkout returns a result (authorized or blocked).
"""

MAX_LLM_STEPS = 12     # guardrail
_ATTEMPTS_PER_MODEL = 3
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}

_client: httpx.Client | None = None


def _limiter_client() -> httpx.Client:
    """Process-wide client (thread-safe in httpx), reused across sessions."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=45)
    return _client


def _backoff(attempt: int) -> float:
    return min(1.0 * (2**attempt), 8.0)


class LLMBrain:
    def __init__(self, intent: str, constraints: dict):
        settings = get_settings()
        # Credential fallback (Fleet PRD B4): run off the strategy provider when
        # AGENT_LLM_* is not configured - the demo then needs only one key pair.
        api_key = settings.agent_llm_api_key or settings.strategy_llm_api_key
        base_url = (
            settings.agent_llm_base_url
            or settings.strategy_llm_base_url
            or "https://api.openai.com/v1"
        ).rstrip("/")
        if not api_key or not base_url:
            raise RuntimeError("LLMBrain requires AGENT_LLM_* or STRATEGY_LLM_* credentials")
        self._base_url = base_url
        self._api_key = api_key
        self._model_chain = settings.agent_llm_model_chain
        self._messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User intent: {intent}\n"
                    f"Constraints: {json.dumps(constraints)}\n"
                    "Proceed step by step using the tools."
                ),
            },
        ]
        self._tools = build_tools()
        self._pending_call_id: str | None = None
        self._steps = 0

    def next_action(self) -> tuple[str, dict]:
        if self._steps >= MAX_LLM_STEPS:
            raise RuntimeError("Agent exceeded maximum tool-call budget")
        self._steps += 1

        response = self._chat()
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            raise LookupError(message.get("content") or "Agent stopped without completing checkout")

        self._messages.append(message)
        first = calls[0]
        args = json.loads(first["function"]["arguments"] or "{}")
        self._pending_call_id = first.get("id")
        return first["function"]["name"], args

    def record_tool_result(self, result: dict | None, error: str | None = None) -> None:
        content = json.dumps({"error": error} if error else result)
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": self._pending_call_id,
                "content": content,
            }
        )
        if error is not None:
            raise RuntimeError(error)

    def _chat(self) -> dict:
        """One model-call round with retries + cross-model fallback, synchronously.

        Mirrors app.llm.openai_compat semantics so the buyer brain inherits the
        same provider resilience the strategy fleet already enjoys.
        """
        errors: list[str] = []
        body = {
            "messages": self._messages,
            "tools": [t.openai_schema() for t in self._tools],
            "temperature": 0,
        }
        client = _limiter_client()
        for model in self._model_chain:
            for attempt in range(_ATTEMPTS_PER_MODEL):
                try:
                    resp = client.post(
                        f"{self._base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={**body, "model": model},
                    )
                except httpx.HTTPError as exc:
                    if attempt < _ATTEMPTS_PER_MODEL - 1:
                        time.sleep(_backoff(attempt))
                        continue
                    errors.append(f"{model}: network {type(exc).__name__}")
                    break
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = None  # non-JSON 200 body (proxy error page, etc.)
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if (
                        not isinstance(choices, list)
                        or not choices
                        or not isinstance(choices[0], dict)
                        or not isinstance(choices[0].get("message"), dict)
                    ):
                        # Same 200-with-error-envelope case as the strategy
                        # provider: skip to the next model instead of crashing.
                        logger.warning(
                            "agent llm model %s returned HTTP 200 without a valid choices payload",
                            model,
                        )
                        errors.append(f"{model}: 200-without-choices")
                        break  # next model
                    return data
                detail = resp.text[:300]
                if resp.status_code in _TRANSIENT_STATUS and attempt < _ATTEMPTS_PER_MODEL - 1:
                    time.sleep(_backoff(attempt))
                    continue
                errors.append(f"{model}: HTTP {resp.status_code} {detail}")
                break
        raise RuntimeError("agent LLM failed: " + " | ".join(errors))
