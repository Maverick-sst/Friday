"""M4 tests: five-agent runtime, baseline graph, evidence traceability.

Uses FakeLLM (deterministic) + MockToolPlane so tests exercise the full
handler path without external calls (PRD_3 §34 scenario coverage).
"""

import pytest

import app.engine.handlers  # noqa: F401  (stub)
from app.db.models import (
    AgentRun,
    BaselineSnapshot,
    Finding,
    Mission,
    Recommendation,
)
from app.engine.executor import execute_mission
from app.engine.queue import InProcessJobQueue
from app.intel import handlers as intel_handlers
from app.intel.handlers import register_all as _register_intel_handlers
from app.intel.schemas import (
    BuyerSimulationOutput,
    ResearchOutput,
    StrategySynthesisOutput,
)

_register_intel_handlers()


class FakeLLM:
    """Deterministic provider: returns schema-shaped canned outputs."""

    def __init__(self):
        self.calls = 0

    async def generate(self, messages, *, max_tokens=1200, temperature=0.4):
        from app.llm.provider import LLMResponse

        self.calls += 1
        return LLMResponse(text="ok", model_used="fake")

    async def structured_generate(self, messages, schema, *, max_tokens=2000, temperature=0.2):
        from app.llm.provider import LLMResponse

        self.calls += 1
        prompt = json_dumps(messages)
        if schema is StrategySynthesisOutput:
            out = StrategySynthesisOutput(
                summary="Two prioritized moves.",
                recommendations=[
                    {
                        "problem": "Delivery uncertainty reduces buyer preference",
                        "why_it_matters": "Buyers choose competitors with explicit dates",
                        "recommendation_text": "Test destination-aware delivery messaging",
                        "expected_impact": "Higher simulated selection rate",
                        "confidence": 0.8,
                        "impact": "high",
                        "finding_titles": ["Competitor communicates delivery certainty better"],
                        "suggested_next_mission": "Run delivery-message counterfactual simulation",
                    },
                    {
                        "problem": "Unknown positioning gap",
                        "why_it_matters": "Could matter",
                        "recommendation_text": "Investigate further",
                        "expected_impact": "Unclear",
                        "confidence": 0.3,
                        "impact": "low",
                    },
                ],
                confidence=0.75,
            )
            return out, LLMResponse(text="", model_used="fake")
        if schema is BuyerSimulationOutput:
            selected = (
                "Competitor X Downshifter 14"
                if "competitor" not in prompt
                else "Velocity Sports Revolution 7"
            )
            out = BuyerSimulationOutput(
                summary=f"Simulated purchase complete ({len(prompt)} chars of context).",
                persona_used="budget-conscious beginner",
                candidates=[
                    {"name": "Nike Revolution 7", "merchant_or_url": "velocitysports.example.com"},
                    {"name": "Competitor X Downshifter 14", "merchant_or_url": "competitor-x.com"},
                ],
                selected=selected,
                ranking=["Competitor X Downshifter 14", "Nike Revolution 7"],
                rejection_reasons={"Nike Revolution 7": "no delivery date shown"},
                friction_observed=["delivery date missing on product page"],
                claims=[
                    {
                        "claim": "Product page shows no delivery estimate",
                        "source_url": "https://velocitysports.example.com/rev-7",
                        "epistemic_state": "fact",
                        "confidence": 0.9,
                    }
                ],
                findings=[
                    {
                        "title": "Merchant page lacks delivery estimate",
                        "statement": "Buyer hesitated due to missing delivery date.",
                        "severity": "high",
                        "confidence": 0.85,
                        "claim_indexes": [0],
                    }
                ],
                confidence=0.7,
            )
            return out, LLMResponse(text="", model_used="fake")
        # ResearchOutput default
        out = ResearchOutput(
            summary="Research finished.",
            claims=[
                {
                    "claim": "Competitor advertises free two-day delivery",
                    "source_url": "https://competitor-x.com",
                    "epistemic_state": "fact",
                    "confidence": 0.95,
                },
                {
                    "claim": "Explicit delivery messaging may improve preference",
                    "epistemic_state": "inference",
                    "confidence": 0.6,
                },
            ],
            findings=[
                {
                    "title": "Competitor communicates delivery certainty better",
                    "statement": "Competitor X shows explicit arrival dates; merchant does not.",
                    "severity": "high",
                    "confidence": 0.8,
                    "claim_indexes": [0],
                }
            ],
            confidence=0.72,
        )
        return out, LLMResponse(text="", model_used="fake")


def json_dumps(messages) -> str:
    import json

    return json.dumps(messages)[:4000]


class StubMemory:
    def __init__(self):
        self.items = []

    async def add(self, merchant_id, text, *, kind="observation", mission_id=None, metadata=None):
        self.items.append((merchant_id, kind, text))
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
def intel_env(monkeypatch, async_db, session_factory):
    """Wire handler seams to fakes bound to the shared sqlite DB."""

    fake_llm = FakeLLM()
    stub_memory = StubMemory()
    monkeypatch.setattr(intel_handlers, "_get_llm", lambda: fake_llm)
    monkeypatch.setattr(intel_handlers, "_get_memory", lambda: stub_memory)
    monkeypatch.setattr(
        intel_handlers,
        "_get_plane",
        lambda: __import__("app.tools.mock_plane", fromlist=["MockToolPlane"]).MockToolPlane(),
    )
    monkeypatch.setattr(intel_handlers, "_session_factory", lambda: session_factory())
    return {"llm": fake_llm, "memory": stub_memory}


async def _make_baseline_mission(async_db, merchant_row, *, budget=25) -> str:
    mission = Mission(
        merchant_id=merchant_row.id,
        name="Baseline",
        objective="Run Day-0 diagnostic for the store",
        mission_type="baseline",
        status="QUEUED",
        budget_runs=budget,
        max_runtime_seconds=120,
    )
    async_db.add(mission)
    await async_db.commit()
    return mission.id


async def test_baseline_end_to_end_with_traceability(
    async_db, merchant_row, session_factory, intel_env, queue
):
    """§34 Scenario 8: multiple findings become ranked recommendations."""
    mission_id = await _make_baseline_mission(async_db, merchant_row)
    status = await execute_mission(mission_id, queue, "w1", session_factory=session_factory)
    assert status == "COMPLETED"

    refreshed = await async_db.get(Mission, mission_id)
    await async_db.refresh(refreshed)
    assert refreshed.status == "COMPLETED"
    assert refreshed.result_summary_json["snapshot_version"] == 1

    # Runs recorded for every specialist phase.
    runs = (
        await async_db.scalars(
            __import__("sqlalchemy").select(AgentRun).where(AgentRun.mission_id == mission_id)
        )
    ).all()
    agents_used = {r.agent_key for r in runs}
    assert {"market", "competitor", "presence", "buyer", "strategy"} <= agents_used
    strategy_run = next(r for r in runs if r.agent_key == "strategy")
    assert strategy_run.result_json["recommendations"]

    # Evidence -> Findings -> Recommendations fully linked.
    findings = (
        await async_db.scalars(
            __import__("sqlalchemy").select(Finding).where(Finding.mission_id == mission_id)
        )
    ).all()
    assert len(findings) >= 4  # research agents + buyer produced findings
    linked_evidence = [f for f in findings if f.evidence_ids_json]
    assert linked_evidence, "findings must cite evidence"

    recs = (
        await async_db.scalars(
            __import__("sqlalchemy").select(Recommendation).where(Recommendation.mission_id == mission_id)
        )
    ).all()
    assert len(recs) == 2
    ranked = sorted(recs, key=lambda r: r.priority_rank)
    assert ranked[0].finding_ids_json, "top recommendation must cite findings"
    assert ranked[0].is_hypothesis is False
    assert ranked[1].is_hypothesis is True  # no finding links -> hypothesis

    # Snapshot v1 written and versioned.
    snaps = (await async_db.scalars(__import__("sqlalchemy").select(BaselineSnapshot))).all()
    assert len(snaps) == 1 and snaps[0].version == 1 and snaps[0].mission_id == mission_id


async def test_budget_exhaustion_stops_spawning(async_db, merchant_row, session_factory, intel_env, queue):
    """§34 Scenario 4: additional agent spawns are rejected when budget gone."""
    mission_id = await _make_baseline_mission(async_db, merchant_row, budget=3)
    status = await execute_mission(mission_id, queue, "w1", session_factory=session_factory)
    refreshed = await async_db.get(Mission, mission_id)
    await async_db.refresh(refreshed)
    assert status in ("PARTIALLY_COMPLETED", "COMPLETED")
    runs = (
        await async_db.scalars(
            __import__("sqlalchemy").select(AgentRun).where(AgentRun.mission_id == mission_id)
        )
    ).all()
    assert len(runs) <= 4  # never exceeded the tiny budget materially


async def test_on_demand_routes_to_strategy(async_db, merchant_row, session_factory, intel_env, queue):
    mission = Mission(
        merchant_id=merchant_row.id,
        name="Why is Competitor X winning beginners?",
        objective="Why is Competitor X outperforming us for beginner customers?",
        mission_type="on_demand",
        status="QUEUED",
        budget_runs=10,
        agent_assignments_json=["market", "buyer"],
        max_runtime_seconds=120,
    )
    async_db.add(mission)
    await async_db.commit()

    status = await execute_mission(mission.id, queue, "w1", session_factory=session_factory)
    assert status == "COMPLETED"
    runs = (
        await async_db.scalars(
            __import__("sqlalchemy").select(AgentRun).where(AgentRun.mission_id == mission.id)
        )
    ).all()
    used = {r.agent_key for r in runs}
    assert {"market", "buyer"} <= used  # assignments respected
    assert "presence" not in used  # unassigned specialists not invoked
    assert "strategy" in used  # synthesis always closes the loop
