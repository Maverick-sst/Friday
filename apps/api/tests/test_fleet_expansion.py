"""Fleet expansion tests (Fleet PRD A1/A2).

Covers: registry completeness, capability-matrix consistency, mock-plane
source-scoped searches, and spawn safety (depth guard + budget recording).
"""

import inspect

import pytest

from app.intel.agents_def import REGISTRY, _spawn_scout, _top_hit_topic
from app.tools.base import CAPABILITIES, AGENT_CAPABILITIES
from app.tools.mock_plane import MockToolPlane

EXPECTED_FLEET = {
    "market",
    "competitor",
    "presence",
    "buyer",
    "strategy",
    "reviews",
    "ads",
    "catalog",
    "scout",
}


def test_registry_has_expanded_fleet():
    assert EXPECTED_FLEET.issubset(set(REGISTRY))


def test_capability_matrix_covers_every_registered_agent():
    for key in REGISTRY:
        assert key in AGENT_CAPABILITIES, f"agent {key!r} missing from AGENT_CAPABILITIES"
        allowed = AGENT_CAPABILITIES[key]
        assert allowed <= CAPABILITIES, f"agent {key!r} references unknown capabilities"


def test_contracts_match_capability_matrix():
    for key, agent in REGISTRY.items():
        contract_tools = set(agent.contract.allowed_tools)
        # "memory" is Strategy's non-web pseudo-tool, not a plane capability.
        assert contract_tools <= CAPABILITIES | {"memory"}, f"{key} contract uses unknown tools"
        matrix = AGENT_CAPABILITIES[key]
        assert contract_tools <= matrix | {"memory"}, (
            f"{key} contract tool {sorted(contract_tools - matrix)} not in matrix"
        )


def test_strategy_stays_off_the_live_web():
    assert AGENT_CAPABILITIES["strategy"] == set()
    assert REGISTRY["strategy"].contract.allowed_tools == ["memory"]


def test_new_source_capabilities_registered():
    assert {"reddit_search", "youtube_search", "social_search"} <= CAPABILITIES


async def test_mock_plane_scoped_searches_are_source_tagged():
    plane = MockToolPlane()
    reddit = await plane.search_reddit("running shoes india")
    youtube = await plane.search_youtube("running shoes review")
    social = await plane.search_social("running shoes offers")
    assert reddit and all(h.source == "reddit" for h in reddit)
    assert youtube and all(h.source == "youtube" for h in youtube)
    assert social and all(h.source == "social" for h in social)


async def test_spawn_guard_blocks_child_agents_from_spawning():
    """Children never spawn (star pattern): depth-1 ctx returns None."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        depth=1, run_id="r1", mission_id="m1", merchant_id="mch", agent_key="anything"
    )
    assert await _spawn_scout(ctx, "objective", "reason") is None


async def test_spawn_guard_blocks_when_depth_limit_zero(monkeypatch):
    from types import SimpleNamespace

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_sub_agent_depth", 0, raising=False)
    ctx = SimpleNamespace(
        depth=0, run_id="r1", mission_id="m1", merchant_id="mch", agent_key="market"
    )
    assert await _spawn_scout(ctx, "objective", "reason") is None


async def test_spawn_records_parent_child_and_budget(monkeypatch):
    """Depth-0 spawn delegates to execute_agent_run with scout/depth/parent set."""
    from types import SimpleNamespace

    captured = {}

    async def fake_execute_agent_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "run_id": "child-1"}

    monkeypatch.setattr(
        "app.intel.handlers.execute_agent_run", fake_execute_agent_run, raising=False
    )
    ctx = SimpleNamespace(
        depth=0, run_id="parent-1", mission_id="m1", merchant_id="mch", agent_key="market"
    )
    result = await _spawn_scout(ctx, "deep-dive the signal", reason="top signal")
    assert result == {"ok": True, "run_id": "child-1"}
    assert captured["agent_key"] == "scout"
    assert captured["depth"] == 1
    assert captured["parent_run_id"] == "parent-1"
    assert captured["extra"]["parent_agent"] == "market"


def test_top_hit_topic_prefers_first_searched_source():
    from types import SimpleNamespace

    obs = SimpleNamespace(
        capability="search_web",
        hits=[SimpleNamespace(url="https://x", title="The Signal")],
    )
    empty = SimpleNamespace(capability="fetch_url", hits=[])
    assert _top_hit_topic([empty, obs]) == "The Signal"
    assert _top_hit_topic([]) is None


def test_baseline_and_on_demand_handlers_include_expanded_fleet():
    from app.intel import handlers

    baseline_src = inspect.getsource(handlers._baseline_handler)
    for key in ("reviews", "ads", "catalog"):
        assert f'"{key}"' in baseline_src


async def test_buyer_sim_mission_type_registered():
    from app.engine.context import registered_types
    from app.intel.handlers import register_all

    register_all()
    assert "buyer_sim" in registered_types()


async def test_buyer_sim_handler_runs_buyer_only(monkeypatch):
    """The quick buyer-sim path runs ONE buyer run and never strategy."""
    from types import SimpleNamespace

    import app.intel.handlers as handlers_mod

    calls: list[dict] = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    async def fake_identity(**kwargs):
        return {"canonical_name": "Velocity Sports"}, False, {}

    async def _no_strategy(*_a, **_k):
        raise AssertionError("strategy synthesis must not run in buyer_sim")

    async def _noop():
        return None

    monkeypatch.setattr(handlers_mod, "execute_agent_run", fake_run)
    monkeypatch.setattr(handlers_mod, "_resolve_or_load_identity", fake_identity)
    monkeypatch.setattr(handlers_mod, "_synthesize", _no_strategy)

    ctx = SimpleNamespace(
        mission_id="m1",
        merchant_id="mch",
        objective="Buy running shoes under Rs 5,000",
        ensure_not_cancelled=_noop,
    )
    summary = await handlers_mod._buyer_sim_handler(ctx)

    assert len(calls) == 1
    assert calls[0]["agent_key"] == "buyer"
    assert calls[0]["depth"] == 0
    assert calls[0]["objective"] == "Buy running shoes under Rs 5,000"
    assert summary["_status"] == "COMPLETED"
    assert summary["specialists"]["buyer"]["ok"] is True
    assert "completed" in summary["summary"].lower()


def test_buyer_runs_get_extended_deadline():
    """Buyer runs stack materialization + a 12-step gateway session: their run
    deadline must exceed the default 300s agent ceiling (mission 33beb1e4
    TIMED_OUT at exactly 300s, orphaning the checkout session mid-flow)."""
    import inspect

    from app.core.config import get_settings
    from app.intel import handlers

    settings = get_settings()
    assert settings.buyer_run_timeout_seconds > settings.agent_run_timeout_seconds
    src = inspect.getsource(handlers.execute_agent_run)
    assert "buyer_run_timeout_seconds" in src  # buyer key branches to the longer ceiling
