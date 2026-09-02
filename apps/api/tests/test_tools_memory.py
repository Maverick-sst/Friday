"""M3 tests: tool router scoping/budgets, mock plane, memory interface."""

import pytest

from app.engine.context import BudgetExhausted, RunBudget
from app.memory.interface import MemoryStore
from app.tools.base import AGENT_CAPABILITIES
from app.tools.mock_plane import MockToolPlane
from app.tools.router import CapabilityDenied, ToolRouter, build_plane


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def test_capability_matrix_matches_prd():
    """PRD_3 §30: Strategy has no live web; all specialists can read."""
    assert AGENT_CAPABILITIES["strategy"] == set()
    for agent in ("market", "competitor", "buyer", "presence"):
        assert "fetch_url" in AGENT_CAPABILITIES[agent]
        assert AGENT_CAPABILITIES[agent] & {"web_search"}


async def test_router_denies_disallowed_capability():
    budget = RunBudget(max_tool_calls=10)
    router = ToolRouter(MockToolPlane(), agent_key="strategy", mission_id="m1", budget=budget)
    with pytest.raises(CapabilityDenied):
        await router.search_web("anything")


async def test_router_enforces_tool_budget():
    budget = RunBudget(max_tool_calls=2)
    router = ToolRouter(MockToolPlane(), agent_key="market", mission_id="m1", budget=budget)
    await router.search_web("running shoes")  # 1
    await router.search_news("running shoes market")  # 2
    with pytest.raises(BudgetExhausted):
        await router.search_web("third call")


async def test_router_records_observations_with_hits():
    budget = RunBudget(max_tool_calls=5)
    router = ToolRouter(MockToolPlane(), agent_key="competitor", mission_id="m1", budget=budget)
    obs = await router.search_shopping("bicycle road bike")
    assert obs.ok is True
    assert len(obs.hits) >= 1
    assert any("Competitor X" in h.title or "GearUp" in h.title for h in obs.hits)


async def test_tool_failure_degrades_gracefully():
    """Scenario 6 - a failing plane yields ok=False observation, no exception."""

    class ExplodingPlane(MockToolPlane):
        async def search_web(self, query):
            raise RuntimeError("provider down")

    budget = RunBudget(max_tool_calls=5)
    router = ToolRouter(ExplodingPlane(), agent_key="market", mission_id="m1", budget=budget)
    obs = await router.search_web("cycling trends")
    assert obs.ok is False
    assert "provider down" in (obs.error or "")


async def test_fetch_url_returns_pages():
    budget = RunBudget(max_tool_calls=3)
    router = ToolRouter(MockToolPlane(), agent_key="presence", mission_id="m1", budget=budget)
    results = await router.fetch_url(["https://competitor-x.com/home", "https://unknown.example/x"])
    assert results[0].text.startswith("Competitor X")
    assert "could not be retrieved" in results[1].text


def test_build_plane_respects_config(monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "composio_api_key", "", raising=False)
    monkeypatch.setattr(s, "composio_enabled", True, raising=False)
    plane = build_plane()
    assert plane.name == "mock"


class _StubStore(MemoryStore):
    def __init__(self):
        self.items = {}

    async def add(self, merchant_id, text, *, kind="observation", mission_id=None, metadata=None):
        self.items.setdefault(merchant_id, []).append((kind, text))
        from app.memory.interface import MemoryWriteResult

        return MemoryWriteResult(accepted=True, provider_memory_ids=["stub-1"])

    async def search(self, merchant_id, query, *, k=5):
        from app.memory.interface import MemoryHit

        words = set(query.lower().split())
        hits = []
        for _kind, text in self.items.get(merchant_id, []):
            overlap = sum(1 for w in words if w in text.lower())
            if overlap:
                hits.append(MemoryHit(id="x", text=text, score=float(overlap)))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:k]

    async def close(self):
        return None


async def test_memory_store_roundtrip():
    store = _StubStore()
    await store.add("m1", "Merchant sells bicycles", kind="fact")
    hits = await store.search("m1", "bicycles")
    assert len(hits) == 1
    assert "bicycles" in hits[0].text.lower()
