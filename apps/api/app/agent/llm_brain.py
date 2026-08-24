"""OpenAI-compatible tool-calling brain.

Activates only when AGENT_LLM_API_KEY (+ base URL/model) is configured. The
model sees the six gateway tools and the user's intent/constraints; it can
propose actions, but amounts, authorization, and execution stay server-side.
"""

import json
import logging

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

MAX_LLM_STEPS = 12


class LLMBrain:
    def __init__(self, intent: str, constraints: dict):
        settings = get_settings()
        if not settings.agent_llm_api_key:
            raise RuntimeError("LLMBrain requires AGENT_LLM_API_KEY")
        self._base_url = (settings.agent_llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self._api_key = settings.agent_llm_api_key
        self._model = settings.agent_llm_model or "gpt-4o-mini"
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
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": self._messages,
                "tools": [t.openai_schema() for t in self._tools],
                "temperature": 0,
            },
            timeout=45,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
        return response.json()
