"""Buyer agent search-fallback tests (plan 1 fix).

Regression: the buyer simulation used to run on a single shopping search —
one empty result (e.g. an over-specific query carrying handler-injected ids)
meant the sim completed blind, with zero product pages and zero evidence.
Now an empty shopping search retries broader, then degrades to web search.
"""

from app.agents.base import RunContext
from app.intel.agents_def import BuyerSimulationAgent
from app.intel.schemas import BuyerSimulationOutput
from app.tools.base import FetchResult, SearchHit
from app.tools.router import ToolRouter
from app.intel.web_catalog import materialize_live_catalog
from types import SimpleNamespace
from contextlib import asynccontextmanager


class FakeBuyerLLM:
    """Records the observation block so tests can assert grounding."""

    def __init__(self):
        self.prompts: list[str] = []

    async def structured_generate(self, messages, schema, *, max_tokens=2000, temperature=0.2):
        self.prompts.append(str(messages))
        out = BuyerSimulationOutput(
            summary="Simulated purchase.",
            persona_used="budget beginner",
            selected="Test Product",
        )
        return out, type("Raw", (), {"model_used": "fake"})()


class RecordingPlane:
    """Fake tool plane with per-capability scripted results + call log."""

    name = "recording"

    def __init__(self, shopping_results, web_results, fetch_results):
        self.shopping_results = shopping_results  # list of lists, popped per call
        self.web_results = web_results
        self.fetch_results = fetch_results
        self.calls: list[str] = []

    async def search_web(self, query):
        self.calls.append(f"web:{query}")
        return (self.web_results or [[]]).pop(0) if self.web_results else []

    async def search_news(self, query):
        self.calls.append(f"news:{query}")
        return []

    async def search_shopping(self, query):
        self.calls.append(f"shopping:{query}")
        if self.shopping_results:
            return self.shopping_results.pop(0)
        return []

    async def search_trends(self, query):
        self.calls.append(f"trends:{query}")
        return []

    async def fetch_url(self, urls, max_chars=6000):
        self.calls.append(f"fetch:{','.join(urls)}")
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def browser_extract(self, url, prompt=None, on_started=None):
        # B6: RecordingPlane has no browser backend; recorded as a call so
        # tests can assert when escalation is attempted, but returns None.
        self.calls.append(f"browser:{url}")
        return None


def _make_ctx(plane, llm) -> tuple[RunContext, ToolRouter]:
    from app.engine.context import RunBudget

    budget = RunBudget(max_tool_calls=12)
    router = ToolRouter(plane, agent_key="buyer", mission_id="m1", budget=budget)
    ctx = RunContext(
        mission_id="m1",
        run_id="r1",
        merchant_id="mer1",
        agent_key="buyer",
        objective="Baseline buyer simulation",
        depth=0,
        parent_run_id=None,
        contract=BuyerSimulationAgent().contract,
        budget_tool_calls=12,
        deadline_seconds=60.0,
        memory=None,
        tools=router,
        llm=llm,
        merchant_context={
            "name": "Velocity Sports",
            "website_url": "https://velocitysports.example.com",
            "category": "Running Shoes",
        },
        extra={"buyer_mission": "Find and choose a product matching this store's main category."},
    )
    return ctx, router


# --- Shared helpers for the transactable-boundary tests ---------------------

_JSONLD_PAGE = (
    '<script type="application/ld+json">'
    '{"@type":"Product","name":"Velocity Racer","offers":'
    '{"price":"3999","priceCurrency":"INR",'
    '"availability":"https://schema.org/InStock"}}'
    "</script>"
)


def _merchant():
    """Minimal merchant-shaped object (materializer only uses getattr)."""
    return SimpleNamespace(
        id="mer1",
        name="Velocity Sports",
        website_url="https://velocitysports.example.com",
    )


def _on_domain_hit(path: str, title: str = "Velocity Racer") -> SearchHit:
    return SearchHit(
        f"https://velocitysports.example.com/{path}",
        title,
        "Buyable running shoe",
        source="shopping",
    )


def _router(plane) -> ToolRouter:
    from app.engine.context import RunBudget

    return ToolRouter(
        plane,
        agent_key="buyer",
        mission_id="m1",
        budget=RunBudget(max_tool_calls=12),
    )


async def test_materializer_falls_back_to_broad_web_search(db_env):
    """Empty shopping + empty site-scoped web -> broad web finds the page."""
    plane = RecordingPlane(
        shopping_results=[[]],
        web_results=[
            [],
            [_on_domain_hit("p1")],
        ],
        fetch_results=[
            [FetchResult("https://velocitysports.example.com/p1", _JSONLD_PAGE)]
        ],
    )
    router = _router(plane)
    result = await materialize_live_catalog(
        db_env["session"], _merchant(), router, llm=None, query="buy running shoes"
    )
    assert result.transactable
    assert len(result.products) == 1
    assert result.products[0].price_minor == 399_900  # Rs 3,999
    assert result.products[0].available_for_sale is True  # canonical schema.org InStock
    assert result.products[0].method == "jsonld"
    assert sum(c.startswith("shopping:") for c in plane.calls) == 1
    assert sum(c.startswith("web:") for c in plane.calls) == 2  # site-scoped + broad
    assert sum(c.startswith("fetch:") for c in plane.calls) == 1
    assert router.budget.tool_calls_used == 4


async def test_materializer_skips_broad_retry_when_shopping_has_on_domain_hits(db_env):
    """Happy path: a same-domain shopping hit materializes immediately."""
    plane = RecordingPlane(
        shopping_results=[[_on_domain_hit("p1")]],
        web_results=[[]],
        fetch_results=[
            [FetchResult("https://velocitysports.example.com/p1", _JSONLD_PAGE)]
        ],
    )
    router = _router(plane)
    result = await materialize_live_catalog(
        db_env["session"], _merchant(), router, llm=None, query="buy running shoes"
    )
    assert result.transactable
    assert len(result.products) == 1
    assert result.products[0].available_for_sale is True
    # No broad retry when the first same-domain hit appears: web called exactly once.
    assert sum(c.startswith("web:") for c in plane.calls) == 1
    assert sum(c.startswith("shopping:") for c in plane.calls) == 1
    assert sum(c.startswith("fetch:") for c in plane.calls) == 1


async def test_materializer_completes_honestly_when_nothing_found(db_env):
    """Worst case: everything empty -> honest NOT-transactable, no fetches."""
    plane = RecordingPlane(shopping_results=[[]], web_results=[[], []], fetch_results=[])
    result = await materialize_live_catalog(
        db_env["session"], _merchant(), _router(plane), llm=None, query="nothing in stock"
    )
    assert result.transactable is False
    assert result.products == []
    assert any("no product pages found" in r for r in result.untransactable_reasons)
    assert sum(c.startswith("shopping:") for c in plane.calls) == 1
    assert sum(c.startswith("web:") for c in plane.calls) == 2
    assert not any(c.startswith("fetch:") for c in plane.calls)


async def test_buyer_execute_wires_the_bridge_outcome(monkeypatch):
    """B3 wiring: `_execute` surfaces the bridge's structured outcome (no crash).

    With no real merchant row reachable, the bridge short-circuits honestly to
    NOT_TRANSACTABLE, and the run still returns a BuyerSimulationOutput carrying it.
    """
    from app.db import session as db_session_mod

    class _NoMerchantResult:
        async def execute(self, _stmt):
            return self

        def scalar_one_or_none(self):
            return None

    @asynccontextmanager
    async def _fake_session():
        yield _NoMerchantResult()

    monkeypatch.setattr(
        db_session_mod, "AsyncSessionLocal", lambda: _fake_session()
    )
    llm = FakeBuyerLLM()
    plane = RecordingPlane(shopping_results=[], web_results=[], fetch_results=[])
    ctx, _router = _make_ctx(plane, llm)

    result, _raw = await BuyerSimulationAgent().execute(ctx)

    assert isinstance(result, BuyerSimulationOutput)
    assert result.transaction is not None
    sessions = result.transaction["sessions"]
    assert sessions and sessions[0]["outcome"] == "NOT_TRANSACTABLE"
    assert any(
        "merchant row not found" in str(reason)
        for reason in (sessions[0].get("reasons") or [])
    )


async def test_transact_bridge_passes_a_real_db_to_the_materializer(monkeypatch):
    """Regression: the materializer must receive a usable AsyncSession.

    It used to be called with the nonexistent ``ctx.db`` — crashing every real
    buyer run with "'RunContext' object has no attribute 'db'" (swallowed into
    NOT_TRANSACTABLE). The bridge must hand over its own session instead.
    """
    import app.intel.web_catalog as web_catalog_mod
    from app.intel.web_catalog import MaterializationResult

    captured: dict = {}

    async def _fake_materialize(db, merchant, tools, llm=None, query=None, domain=None):
        captured["db"] = db
        captured["merchant_id"] = getattr(merchant, "id", None)
        captured["tools"] = tools
        return MaterializationResult(products=[], untransactable_reasons=["fake unit-test reasons"])

    monkeypatch.setattr(web_catalog_mod, "materialize_live_catalog", _fake_materialize)

    class _MerchantRowResult:
        async def execute(self, _stmt):
            return self

        def scalar_one_or_none(self):
            return SimpleNamespace(
                id="mer1",
                name="Velocity Sports",
                website_url="https://velocitysports.example.com",
            )

    @asynccontextmanager
    async def _fake_session():
        yield _MerchantRowResult()

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", lambda: _fake_session())

    llm = FakeBuyerLLM()
    plane = RecordingPlane(shopping_results=[], web_results=[], fetch_results=[])
    ctx, _router = _make_ctx(plane, llm)

    result, _raw = await BuyerSimulationAgent().execute(ctx)

    # The materializer was actually reached and got a session handle + the run's
    # tool router (budget/provenance preserved) — not the missing ctx.db.
    assert captured["db"] is not None
    assert captured["db"] is not ctx
    assert captured["merchant_id"] == "mer1"
    assert captured["tools"] is ctx.tools

    assert isinstance(result, BuyerSimulationOutput)
    sessions = result.transaction["sessions"]
    assert sessions[0]["outcome"] == "NOT_TRANSACTABLE"
    assert "fake unit-test reasons" in (sessions[0].get("untransactable_reasons") or [])
