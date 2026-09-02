"""Identity resolution + evidence relevance gate tests (FIX_PRD_1 §20-§22).

Reproduces the SNITCH-style ambiguity failure observed during manual
validation: an ambiguous brand name pulling unrelated entities (CI/CD
tooling) into research — and verifies the relevance gate keeps them out of
the evidence/strategy path while legitimate sources survive.
"""

import pytest
from sqlalchemy import select

import app.engine.handlers  # noqa: F401  (stub)
from app.db.models import Evidence, Finding, MerchantProfile, Mission
from app.engine.executor import execute_mission
from app.engine.queue import InProcessJobQueue
from app.intel import handlers as intel_handlers
from app.intel.agents_def import _grounded
from app.intel.handlers import register_all as _register_intel_handlers
from app.intel.schemas import (
    BuyerSimulationOutput,
    IdentityResolutionOutput,
    ResearchOutput,
    StrategySynthesisOutput,
)
from app.tools.base import FetchResult, SearchHit

_register_intel_handlers()


class ScriptedPlane:
    """Deterministic ToolPlane: canned first-party page + verification hits."""

    name = "scripted"

    def __init__(self, first_party_text="", web_hits=None, pages=None):
        self._first_party_text = first_party_text
        self._web_hits = web_hits or []
        self._pages = pages or {}

    async def search_web(self, query):
        return list(self._web_hits)

    async def search_news(self, query):
        return list(self._web_hits)

    async def search_shopping(self, query):
        return list(self._web_hits)

    async def search_trends(self, query):
        return []

    async def search_reddit(self, query):
        return []

    async def search_youtube(self, query):
        return []

    async def search_social(self, query):
        return []

    async def fetch_url(self, urls, max_chars=6000):
        return [FetchResult(url=u, text=self._pages.get(u, self._first_party_text)) for u in urls]


class FakeLLM:
    """Schema-shaped canned outputs incl. per-claim entity_relevance scores."""

    def __init__(self, identity=None, claims=None, findings=None):
        self.identity = identity
        self.claims = claims or []
        self.findings = findings or []

    async def generate(self, messages, *, max_tokens=1200, temperature=0.4):
        from app.llm.provider import LLMResponse

        return LLMResponse(text="ok", model_used="fake")

    async def structured_generate(self, messages, schema, *, max_tokens=2000, temperature=0.2):
        from app.llm.provider import LLMResponse

        if schema is IdentityResolutionOutput:
            return self.identity, LLMResponse(text="", model_used="fake")
        if schema is BuyerSimulationOutput:
            out = BuyerSimulationOutput(
                summary="Bought nothing suitable.", persona_used="budget beginner", confidence=0.6
            )
            return out, LLMResponse(text="", model_used="fake")
        if schema is StrategySynthesisOutput:
            out = StrategySynthesisOutput(summary="Nothing to add.", confidence=0.5)
            return out, LLMResponse(text="", model_used="fake")
        out = ResearchOutput(
            summary="Research finished.",
            claims=self.claims,
            findings=self.findings,
            confidence=0.8,
        )
        return out, LLMResponse(text="", model_used="fake")


class StubMemory:
    def __init__(self):
        self.items = []

    async def add(self, merchant_id, text, *, kind="observation", mission_id=None, metadata=None):
        self.items.append((kind, text))
        from app.memory.interface import MemoryWriteResult

        return MemoryWriteResult(accepted=True, provider_memory_ids=["stub"])

    async def search(self, merchant_id, query, *, k=5):
        return []

    async def close(self):
        return None


@pytest.fixture()
async def queue():
    q = InProcessJobQueue()
    yield q
    await q.close()


@pytest.fixture()
def env(monkeypatch, session_factory):
    """Wire handler seams to per-test fakes (same seams as test_intel_agents)."""
    holder = {}

    def _wire(llm, plane, memory):
        monkeypatch.setattr(intel_handlers, "_get_llm", lambda: llm)
        monkeypatch.setattr(intel_handlers, "_get_memory", lambda: memory)
        monkeypatch.setattr(intel_handlers, "_get_plane", lambda: plane)
        monkeypatch.setattr(intel_handlers, "_session_factory", lambda: session_factory())

    holder["wire"] = _wire
    return holder


async def _make_mission(async_db, merchant_row, *, mission_type="on_demand", budget=25) -> str:
    mission = Mission(
        merchant_id=merchant_row.id,
        name="T",
        objective="Research the merchant",
        mission_type=mission_type,
        status="QUEUED",
        budget_runs=budget,
        max_runtime_seconds=120,
    )
    async_db.add(mission)
    await async_db.commit()
    return mission.id


async def test_identity_packet_resolved_and_stored(async_db, merchant_row, session_factory, env):
    llm = FakeLLM(
        identity=IdentityResolutionOutput(
            canonical_name="SNITCH",
            business_type="fashion ecommerce",
            primary_category="men's fashion",
            geography="India",
            description="Indian D2C men's fashion brand.",
            known_product_types=["shirts", "jeans"],
            official_domains=["snitch.com"],
            identity_confidence=0.95,
            ambiguity_notes=["Snitch CI/CD tooling product"],
        )
    )
    plane = ScriptedPlane(first_party_text="SNITCH - men's fashion India. New arrivals.")
    memory = StubMemory()
    env["wire"](llm, plane, memory)

    mission_id = await _make_mission(async_db, merchant_row)
    identity, degraded, meta = await intel_handlers._resolve_or_load_identity(
        mission_id=mission_id, merchant_id=merchant_row.id, objective="competitor analysis"
    )

    assert degraded is False
    assert identity["canonical_name"] == "SNITCH"
    assert identity["domain"] == "example.com"  # first-party URL is ground truth
    assert "snitch.com" in identity["official_domains"]
    assert "example.com" in identity["official_domains"]
    assert meta["ambiguity_notes"] == ["Snitch CI/CD tooling product"]

    # Packet persisted additively on the profile (no schema migration, §27).
    profile = await async_db.scalar(
        select(MerchantProfile).where(MerchantProfile.merchant_id == merchant_row.id)
    )
    assert profile.extra_json["identity_packet"]["canonical_name"] == "SNITCH"
    assert profile.primary_category == "men's fashion"

    # Only high-confidence identity facts reach semantic memory (§26).
    assert any(kind == "fact" and "SNITCH" in text for kind, text in memory.items)


async def test_low_confidence_identity_degrades_without_memory(
    async_db, merchant_row, session_factory, env
):
    llm = FakeLLM(identity=IdentityResolutionOutput(canonical_name="Snitch", identity_confidence=0.3))
    memory = StubMemory()
    env["wire"](llm, ScriptedPlane(first_party_text="hello"), memory)

    mission_id = await _make_mission(async_db, merchant_row)
    identity, degraded, _meta = await intel_handlers._resolve_or_load_identity(
        mission_id=mission_id, merchant_id=merchant_row.id, objective="competitor analysis"
    )

    assert degraded is True
    assert identity["canonical_name"] == "Snitch"
    assert memory.items == []  # low-confidence identity never becomes "fact" memory


async def test_unscored_claims_still_promoted(async_db, merchant_row, session_factory, env):
    """Backward compatibility: legacy outputs without entity_relevance pass."""
    llm = FakeLLM(
        claims=[
            {
                "claim": "Competitor offers two-day delivery",
                "source_url": "https://competitor-x.com",
                "epistemic_state": "fact",
                "confidence": 0.9,
            }
        ],
        findings=[
            {
                "title": "Competitor delivery speed",
                "statement": "Competitor promises two-day delivery.",
                "severity": "medium",
                "confidence": 0.8,
                "claim_indexes": [0],
            }
        ],
    )
    env["wire"](llm, ScriptedPlane(), StubMemory())

    mission_id = await _make_mission(async_db, merchant_row)
    summary = await intel_handlers.execute_agent_run(
        mission_id=mission_id,
        merchant_id=merchant_row.id,
        agent_key="market",
        objective="Research delivery signals.",
    )
    assert summary["ok"] is True
    stats = summary["relevance_stats"]
    assert stats["claims_unscored"] == 1
    assert stats["claims_rejected"] == 0
    evidence = (await async_db.scalars(select(Evidence).where(Evidence.mission_id == mission_id))).all()
    assert len(evidence) == 1


def test_grounded_queries_use_identity():
    """High confidence: quoted canonical name + category + geography."""

    class _Ctx:
        pass

    high = _Ctx()
    high.merchant_context = {
        "name": "snitch",
        "website_url": "https://www.snitch.com",
        "category": None,
        "identity": {
            "canonical_name": "SNITCH",
            "primary_category": "men's fashion",
            "geography": "India",
            "identity_confidence": 0.95,
        },
    }
    q = _grounded(high, "competitors")
    assert '"SNITCH"' in q
    assert "men's fashion" in q
    assert "India" in q
    assert "competitors" in q
    assert not q.startswith("snitch competitors")  # no name-only lexical query

    low = _Ctx()
    low.merchant_context = {
        "name": "snitch",
        "website_url": "https://www.snitch.com",
        "category": None,
        "identity": {"canonical_name": "SNITCH", "identity_confidence": 0.3, "domain": "snitch.com"},
    }
    q2 = _grounded(low, "competitors")
    assert '"snitch.com"' in q2  # degraded mode anchors on the domain, not the name
    assert "SNITCH" not in q2


async def test_snitch_ambiguity_relevance_gate(async_db, merchant_row, session_factory, env):
    """Canonical repro: fashion merchant, CI/CD homonym noise must be rejected."""
    llm = FakeLLM(
        identity=IdentityResolutionOutput(
            canonical_name="SNITCH",
            primary_category="men's fashion",
            geography="India",
            official_domains=["example.com"],
            identity_confidence=0.95,
        ),
        claims=[
            {  # legitimate third-party coverage of the fashion brand
                "claim": "SNITCH expands its men's fashion line in India",
                "source_url": "https://fashionnews.in/snitch-expansion",
                "epistemic_state": "fact",
                "confidence": 0.9,
                "entity_relevance": 0.95,
            },
            {  # factually true, about a DIFFERENT "Snitch" (CI/CD tooling)
                "claim": "Snitch CI/CD tool automates deployment pipelines",
                "source_url": "https://devopsweekly.com/snitch-tool",
                "epistemic_state": "fact",
                "confidence": 0.93,  # high factual confidence, irrelevant entity
                "entity_relevance": 0.1,
            },
            {  # official first-party page
                "claim": "Official site lists new arrivals for men's fashion",
                "source_url": "https://www.snitch.com/new-arrivals",
                "epistemic_state": "fact",
                "confidence": 0.99,
                "entity_relevance": 1.0,
            },
        ],
        findings=[
            {
                "title": "SNITCH is expanding in India",
                "statement": "Third-party coverage confirms expansion.",
                "severity": "medium",
                "confidence": 0.85,
                "claim_indexes": [0],
            },
            {
                "title": "Snitch CI/CD automates deployments",
                "statement": "Only source is the unrelated tooling article.",
                "severity": "high",
                "confidence": 0.9,
                "claim_indexes": [1],  # every cited claim rejected -> dropped
            },
            {
                "title": "Merchant catalog keeps new arrivals current",
                "statement": "First-party page shows active merchandising.",
                "severity": "low",
                "confidence": 0.8,
                "claim_indexes": [2],
            },
        ],
    )
    plane = ScriptedPlane(
        web_hits=[
            SearchHit(
                "https://devopsweekly.com/snitch-tool",
                "Snitch - CI/CD tooling",
                "Automate deployment pipelines.",
            )
        ],
        first_party_text="SNITCH men's fashion India new arrivals.",
    )
    env["wire"](llm, plane, StubMemory())

    mission_id = await _make_mission(async_db, merchant_row)
    identity, _degraded, _meta = await intel_handlers._resolve_or_load_identity(
        mission_id=mission_id, merchant_id=merchant_row.id, objective="competitor analysis"
    )
    summary = await intel_handlers.execute_agent_run(
        mission_id=mission_id,
        merchant_id=merchant_row.id,
        agent_key="market",
        objective="Perform a complete competitor analysis.",
    )
    assert summary["ok"] is True
    assert identity["canonical_name"] == "SNITCH"
    stats = summary["relevance_stats"]
    assert stats["claims_total"] == 3
    assert stats["claims_rejected"] == 1
    assert stats["findings_dropped"] == 1

    findings = (await async_db.scalars(select(Finding).where(Finding.mission_id == mission_id))).all()
    titles = {f.title for f in findings}
    assert "SNITCH is expanding in India" in titles
    assert "Merchant catalog keeps new arrivals current" in titles
    assert "Snitch CI/CD automates deployments" not in titles  # never reaches strategy

    evidence = (await async_db.scalars(select(Evidence).where(Evidence.mission_id == mission_id))).all()
    claims = {e.claim for e in evidence}
    # The REJECTED claim never became an evidence row (the relevance gate
    # drops it from the claims path); the audit layer (persist_observations)
    # may still mention the term in a scout's search-query audit row — by
    # design, observations are the unfiltered audit trail (FIX_PRD_1 §13).
    assert not any("CI/CD automates" in c for c in claims)  # rejected claim text never became evidence
    assert any("fashion line" in c for c in claims)  # relevant claim promoted


async def test_baseline_low_confidence_identity_degraded_flag(
    async_db, merchant_row, session_factory, env, queue
):
    llm = FakeLLM(
        identity=IdentityResolutionOutput(canonical_name="Example", identity_confidence=0.3),
        claims=[
            {
                "claim": "Market context noted",
                "source_url": "https://web.example/x",
                "epistemic_state": "inference",
                "confidence": 0.7,
                "entity_relevance": 0.6,
            }
        ],
        findings=[
            {
                "title": "Market context",
                "statement": "Generic category signal.",
                "severity": "low",
                "confidence": 0.6,
                "claim_indexes": [0],
            }
        ],
    )
    env["wire"](llm, ScriptedPlane(first_party_text="hello"), StubMemory())

    mission_id = await _make_mission(async_db, merchant_row, mission_type="baseline")
    status = await execute_mission(mission_id, queue, "w1", session_factory=session_factory)
    refreshed = await async_db.get(Mission, mission_id)
    await async_db.refresh(refreshed)
    assert status in ("COMPLETED", "PARTIALLY_COMPLETED")
    assert refreshed.result_summary_json["identity"]["degraded"] is True
    assert refreshed.result_summary_json["quality"]["claims_total"] >= 1


async def test_baseline_quality_metrics_reported(async_db, merchant_row, session_factory, env, queue):
    llm = FakeLLM(
        identity=IdentityResolutionOutput(
            canonical_name="Example",
            primary_category="fashion",
            geography="India",
            official_domains=["example.com"],
            identity_confidence=0.95,
        ),
        claims=[
            {
                "claim": "Relevant market signal",
                "source_url": "https://fashionnews.in/x",
                "epistemic_state": "fact",
                "confidence": 0.9,
                "entity_relevance": 0.9,
            },
            {
                "claim": "Irrelevant homonym signal",
                "source_url": "https://tools.example/y",
                "epistemic_state": "fact",
                "confidence": 0.95,
                "entity_relevance": 0.05,
            },
        ],
        findings=[
            {
                "title": "Relevant signal",
                "statement": "Category context confirmed.",
                "severity": "low",
                "confidence": 0.7,
                "claim_indexes": [0],
            }
        ],
    )
    env["wire"](llm, ScriptedPlane(), StubMemory())

    mission_id = await _make_mission(async_db, merchant_row, mission_type="baseline")
    status = await execute_mission(mission_id, queue, "w1", session_factory=session_factory)
    refreshed = await async_db.get(Mission, mission_id)
    await async_db.refresh(refreshed)
    assert status in ("COMPLETED", "PARTIALLY_COMPLETED")
    quality = refreshed.result_summary_json["quality"]
    assert quality["claims_rejected"] >= 1
    assert quality["entity_relevance_rate"] is not None and quality["entity_relevance_rate"] < 1.0
    assert refreshed.result_summary_json["identity"]["degraded"] is False
