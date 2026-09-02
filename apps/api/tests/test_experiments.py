"""M5 tests: counterfactual experiments (PRD_3 §19 + §34 scenario coverage).

Verifies: both arms execute over the same cohort, selections are scored,
results are stored with SIMULATED labels, and API lifecycle works.
"""

import pytest

from app.db.models import Experiment, ExperimentRun, Mission
from app.engine.executor import execute_mission
from app.engine.queue import InProcessJobQueue
from app.intel import handlers as intel_handlers
from app.intel.experiments import register_experiment_handler
from app.intel.handlers import register_all as _register_intel

_register_intel()
register_experiment_handler()

from tests.test_intel_agents import FakeLLM, StubMemory  # noqa: E402


@pytest.fixture()
def intel_env(monkeypatch, async_db, session_factory):
    fake_llm = FakeLLM()
    stub_memory = StubMemory()

    monkeypatch.setattr(intel_handlers, "_get_llm", lambda: fake_llm)
    monkeypatch.setattr(intel_handlers, "_get_memory", lambda: stub_memory)
    from app.tools.mock_plane import MockToolPlane

    monkeypatch.setattr(intel_handlers, "build_plane", lambda: MockToolPlane())
    monkeypatch.setattr(intel_handlers, "_session_factory", lambda: session_factory())
    return {"llm": fake_llm}


@pytest.fixture()
async def queue():
    q = InProcessJobQueue()
    yield q
    await q.close()


async def _make_experiment(async_db, merchant_row, *, cohort=4) -> Experiment:
    exp = Experiment(
        merchant_id=merchant_row.id,
        hypothesis="Explicit delivery messaging increases buyer selection for running shoes",
        control_variant_json={"product": "Velocity Sports Revolution 7", "messaging": "Fast shipping"},
        treatment_variant_json={
            "product": "Velocity Sports Revolution 7",
            "messaging": "Arrives by Thursday",
        },
        cohort_size=cohort,
        status="CREATED",
    )
    async_db.add(exp)
    await async_db.commit()
    return exp


async def _make_experiment_mission(async_db, merchant_row, exp) -> str:
    mission = Mission(
        merchant_id=merchant_row.id,
        name="Experiment run",
        objective=f"Simulated counterfactual: {exp.hypothesis}",
        mission_type="experiment",
        status="QUEUED",
        budget_runs=50,
        max_runtime_seconds=120,
    )
    async_db.add(mission)
    await async_db.flush()
    exp.mission_id = mission.id
    exp.status = "QUEUED"
    await async_db.commit()
    return mission.id


async def test_experiment_runs_both_arms_and_labels_simulated(
    async_db, merchant_row, session_factory, intel_env, queue
):
    """§34: control/variant simulation works; results stored; labeled simulated."""
    exp = await _make_experiment(async_db, merchant_row)
    mission_id = await _make_experiment_mission(async_db, merchant_row, exp)

    status = await execute_mission(mission_id, queue, "w1", session_factory=session_factory)
    assert status == "COMPLETED"

    runs = (await async_db.scalars(__import__("sqlalchemy").select(ExperimentRun))).all()
    arms = [r.arm for r in runs]
    assert arms.count("control") == 4  # full cohort ran on each arm
    assert arms.count("treatment") == 4

    refreshed_exp = await async_db.get(Experiment, exp.id)
    await async_db.refresh(refreshed_exp)
    assert refreshed_exp.is_simulated is True
    assert refreshed_exp.result_json["SIMULATED"] is True
    assert "NOT real production revenue" in refreshed_exp.result_json["note"]
    assert "control_selection_rate" in refreshed_exp.result_json
    assert "simulated_relative_lift_pct" in refreshed_exp.result_json

    mission = await async_db.get(Mission, mission_id)
    await async_db.refresh(mission)
    assert mission.result_summary_json["SIMULATED"] is True


async def test_experiment_api_lifecycle(api):
    client = api["client"]
    onboard = (await client.post("/api/v1/team/onboard", json={"url": "https://exp.example.com"})).json()

    # Unknown type guard still works for missions; experiments have their own route.
    res = await client.post(
        "/api/v1/team/experiments",
        json={
            "merchant_id": onboard["merchant_id"],
            "hypothesis": "Delivery date badges increase selection",
            "control_variant_json": {"messaging": "Fast shipping"},
            "treatment_variant_json": {"messaging": "Arrives by Thursday"},
            "cohort_size": 4,
        },
        headers={"Idempotency-Key": "exp-1"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["SIMULATED"] is True

    replay = await client.post(
        "/api/v1/team/experiments",
        json={"merchant_id": onboard["merchant_id"], "hypothesis": "Delivery date badges increase selection"},
        headers={"Idempotency-Key": "exp-1"},
    )
    assert replay.json()["idempotent_replay"] is True

    detail = await client.get(f"/api/v1/team/experiments/{body['experiment_id']}")
    assert detail.json()["is_simulated"] is True
