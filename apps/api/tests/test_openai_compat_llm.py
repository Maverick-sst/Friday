"""OpenAICompatProvider tests against a mocked REST transport.

Regression coverage: OpenRouter occasionally answers HTTP 200 with an error
envelope (no "choices" key) when the upstream provider fails. This must be
treated as a dead model so the fallback chain continues, never as a KeyError
that aborts the agent run.
"""

import json

import httpx
import pytest

from app.llm.openai_compat import LLMPermanentError, OpenAICompatProvider


def _completion(text: str, model: str) -> dict:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _error_envelope(message: str) -> dict:
    """What OpenRouter returns with HTTP 200 when the upstream provider dies."""
    return {"error": {"message": message, "code": 502}}


def _make_provider(responses: list[httpx.Response]) -> tuple[OpenAICompatProvider, list[dict]]:
    """Provider with a 2-model chain whose calls replay `responses` in order."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode()))
        return responses.pop(0)

    provider = object.__new__(OpenAICompatProvider)  # bypass settings/client init
    provider._model_chain = ["model-a", "model-b"]
    # Absolute base_url mirrors production (settings.strategy_llm_base_url);
    # without it the relative "/chat/completions" URL breaks httpx's
    # cookie-jar URL parsing ("unknown url type: '/chat/completions'").
    provider._client = httpx.AsyncClient(
        base_url="https://mock-llm.test/v1", transport=httpx.MockTransport(handler)
    )
    return provider, sent


async def test_200_error_envelope_falls_through_to_next_model():
    """A 200 with no 'choices' skips that model; the chain still completes."""
    provider, sent = _make_provider(
        [
            httpx.Response(200, json=_error_envelope("upstream provider failed")),
            httpx.Response(200, json=_completion("hello from b", "model-b")),
        ]
    )
    resp = await provider.generate([{"role": "user", "content": "hi"}])
    assert resp.text == "hello from b"
    assert resp.model_used == "model-b"
    # model-a was tried exactly once (no wasted retries), then model-b succeeded.
    assert [call["model"] for call in sent] == ["model-a", "model-b"]


async def test_non_json_200_body_falls_through_to_next_model():
    """A 200 whose body is not JSON (proxy error page) is also survivable."""
    provider, sent = _make_provider(
        [
            httpx.Response(200, text="<html>Bad Gateway</html>"),
            httpx.Response(200, json=_completion("recovered", "model-b")),
        ]
    )
    resp = await provider.generate([{"role": "user", "content": "hi"}])
    assert resp.text == "recovered"
    assert [call["model"] for call in sent] == ["model-a", "model-b"]


async def test_all_models_return_200_error_envelopes_raises_permanent_error():
    """If every model fails the same way, raise LLMPermanentError with detail."""
    provider, sent = _make_provider(
        [
            httpx.Response(200, json=_error_envelope("a down")),
            httpx.Response(200, json=_error_envelope("b down")),
        ]
    )
    with pytest.raises(LLMPermanentError) as excinfo:
        await provider.generate([{"role": "user", "content": "hi"}])
    assert "200-without-choices" in str(excinfo.value)
    # Each dead model is tried once per call, not 3x2 times.
    assert [call["model"] for call in sent] == ["model-a", "model-b"]


async def test_valid_200_still_returns_completion():
    """Happy path unchanged: a well-formed 200 is parsed as before."""
    provider, sent = _make_provider(
        [httpx.Response(200, json=_completion("all good", "model-a"))]
    )
    resp = await provider.generate([{"role": "user", "content": "hi"}])
    assert resp.text == "all good"
    assert resp.model_used == "model-a"
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 5
    assert len(sent) == 1
