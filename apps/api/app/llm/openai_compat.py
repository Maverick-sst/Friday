"""OpenAI-compatible LLM provider (covers OpenRouter, OpenAI, Groq, vLLM...).

Design notes (PRD_3 §23.9 external-tool isolation):
- Every call has a request timeout, bounded retries with exponential backoff
  and jitter on transient failures (429 / 5xx / network), and a model
  fallback chain (primary first, then configured fallbacks).
- Structured generation prompts for strict JSON, validates against the given
  pydantic schema, and performs one deterministic repair round on failure.
- A global concurrency limiter caps simultaneous in-flight calls.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.engine.limits import llm_limiter
from app.llm.provider import LLMProvider, LLMResponse, Message

logger = logging.getLogger("acg.llm.openai_compat")

# Ordered model fallback chain: tried top to bottom. A model that fails
# permanently (paid / removed / bad request) is skipped for the rest of the
# call and the chain continues — one dead model can never abort a mission.
# Env-configured models (STRATEGY_LLM_MODEL / _FALLBACK_MODELS) are prepended
# to this list; these defaults are the guaranteed safety net.
DEFAULT_MODEL_FALLBACK_CHAIN = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "minimax/minimax-m3:free",
    "nvidia/nemotron-3.5-lightning:free",
    "openrouter/free",
    "z-ai/glm-5.2:free",
]

_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
# Free/shared upstream pools (OpenRouter :free) throttle in bursts; be patient:
# several attempts per model with long-jitter backoff, plus two full chain
# passes before declaring failure.
_ATTEMPTS_PER_MODEL = 3
_CHAIN_PASSES = 2


class LLMPermanentError(Exception):
    """Non-retryable provider failure (auth, unknown model, bad request)."""


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from a model response."""
    text = text.strip()
    if text.startswith("```"):
        # ```json ... ``` fence
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} block.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no parsable JSON object in response: {text[:200]!r}")


class OpenAICompatProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_configured:
            raise RuntimeError("LLM not configured; check STRATEGY_LLM_* settings")
        self._client = httpx.AsyncClient(
            base_url=settings.strategy_llm_base_url,
            headers={
                "Authorization": f"Bearer {settings.strategy_llm_api_key}",
                "Content-Type": "application/json",
                # Optional OpenRouter attribution headers; harmless elsewhere.
                "HTTP-Referer": settings.web_origin,
                "X-Title": "Agent Commerce Strategy Team",
            },
            timeout=httpx.Timeout(90.0, connect=10.0),
        )
        # Env chain first (primary + STRATEGY_LLM_FALLBACK_MODELS), then the
        # built-in defaults as a deduplicated, order-kept safety net.
        chain = list(settings.llm_model_chain)
        for model in DEFAULT_MODEL_FALLBACK_CHAIN:
            if model not in chain:
                chain.append(model)
        self._model_chain = chain or ["gpt-4o-mini"]

    async def generate(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.4,
    ) -> LLMResponse:
        # Observability (PRD 9.3/§27): one generation observation per call,
        # covering the full model fallback chain (fallbacks visible as attrs).
        from app.core.config import get_settings as _gs
        from app.observability import enabled as _otel_enabled, observation

        _settings = _gs()
        if _settings.trace_llm_payloads and _otel_enabled():
            gen_input: Any = [
                {"role": m.get("role", "user"), "content": (m.get("content") or "")[-4000:]}
                for m in messages
            ]
        else:
            gen_input: Any = {"_policy": "metadata-only"}  # PRD 24
        with observation(
            name="llm.generate",
            as_type="generation",
            input=gen_input,
            model=",".join(self._model_chain),
            provider="openai_compat",
        ):
            return await self._generate_chain(
                messages, max_tokens=max_tokens, temperature=temperature
            )

    async def _generate_chain(self, messages, *, max_tokens=1200, temperature=0.4) -> LLMResponse:
        errors: list[str] = []
        skipped: set[str] = set()  # models that failed permanently during this call
        for _pass in range(_CHAIN_PASSES):
            for model in self._model_chain:
                if model in skipped:
                    continue  # dead model: don't waste a request on it again
                for attempt in range(_ATTEMPTS_PER_MODEL):
                    started = time.monotonic()
                    try:
                        async with llm_limiter().acquire_context(
                            "acg:llc:global", get_settings().max_llm_concurrency_global
                        ):
                            resp = await self._client.post(
                                "/chat/completions",
                                json={
                                    "model": model,
                                    "messages": messages,
                                    "max_tokens": max_tokens,
                                    "temperature": temperature,
                                },
                            )
                        latency_ms = int((time.monotonic() - started) * 1000)
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
                                # Gateways like OpenRouter occasionally answer
                                # HTTP 200 with an error envelope (upstream
                                # provider failure, moderation) that has no
                                # "choices" key. Treat it like a dead model so
                                # the fallback chain continues instead of a
                                # KeyError aborting the whole agent run.
                                detail = _safe_detail(resp)
                                logger.warning(
                                    "llm model %s returned HTTP 200 without a valid choices payload (%s)",
                                    model,
                                    detail,
                                )
                                errors.append(f"{model}: 200-without-choices {detail}")
                                skipped.add(model)
                                break  # next model in the chain
                            choice = choices[0]["message"].get("content")
                            usage = data.get("usage") or {}
                            return LLMResponse(
                                text=choice or "",
                                model_used=data.get("model", model),
                                prompt_tokens=usage.get("prompt_tokens"),
                                completion_tokens=usage.get("completion_tokens"),
                                latency_ms=latency_ms,
                            )
                        detail = _safe_detail(resp)
                        if resp.status_code in _TRANSIENT_STATUS and attempt < _ATTEMPTS_PER_MODEL - 1:
                            await asyncio.sleep(
                                _backoff(attempt, retry_after=resp.headers.get("retry-after"))
                            )
                            continue
                        if resp.status_code in _TRANSIENT_STATUS:
                            errors.append(f"{model}: {resp.status_code} {detail}")
                            break  # next model
                        # Permanent failure for THIS model (402 paid / 404 gone /
                        # 400 bad request): skip it and fall through to the next
                        # model instead of aborting the whole chain.
                        logger.warning("llm model %s permanently failed (%s); skipping", model, detail)
                        skipped.add(model)
                        errors.append(f"{model}: {resp.status_code} {detail}")
                        break
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        if attempt < _ATTEMPTS_PER_MODEL - 1:
                            await asyncio.sleep(_backoff(attempt))
                            continue
                        errors.append(f"{model}: network {type(exc).__name__}")
                        break
        raise LLMPermanentError("all models failed: " + " | ".join(errors))

    async def structured_generate(
        self,
        messages: list[Message],
        schema: type[BaseModel],
        *,
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> tuple[BaseModel, LLMResponse]:
        schema_json = json.dumps(schema.model_json_schema(), default=str)
        system_hint = (
            "Return ONLY a single JSON object that validates against this JSON Schema. "
            "No prose, no markdown fences. Keep every string value concise "
            "(summary under 50 words, statements under 25 words, at most 4 claims "
            "and 3 findings).\nSchema:\n" + schema_json
        )
        augmented: list[Message] = []
        if messages and messages[0].get("role") == "system":
            augmented.append({"role": "system", "content": messages[0]["content"] + "\n\n" + system_hint})
            augmented.extend(messages[1:])
        else:
            augmented.append({"role": "system", "content": system_hint})
            augmented.extend(messages)

        last_error: Exception | None = None
        raw = None
        for attempt in range(2):  # one repair round
            ask = augmented
            if attempt == 1 and last_error is not None:
                ask = [
                    *augmented,
                    {
                        "role": "user",
                        "content": (
                            f"Your previous reply was invalid: {str(last_error)[:300]}. "
                            "Return a corrected JSON object only."
                        ),
                    },
                ]
            raw = await self.generate(ask, max_tokens=max_tokens, temperature=temperature)
            try:
                if not raw.text.strip():
                    raise ValueError("empty completion (model returned no content)")
                parsed = schema.model_validate(_extract_json(raw.text))
                return parsed, raw
            except (ValueError, ValidationError) as exc:
                last_error = exc
        raise ValueError(
            f"structured output failed schema validation twice ({schema.__name__}): {repr(last_error)[:300]}"
        )


def _backoff(attempt: int, *, retry_after: str | None = None) -> float:
    """Long-jitter backoff tuned for shared free-pool 429 bursts (up to ~45s)."""
    base = min(3.0 * (2**attempt), 30.0)
    jitter = random.uniform(0, base * 0.5)
    if retry_after:
        try:
            return max(float(retry_after), jitter)
        except ValueError:
            pass
    return base + jitter


def _safe_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message", body))[:200]
        return str(err or body)[:200]
    except Exception:
        return resp.text[:200]
