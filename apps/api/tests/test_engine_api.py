"""Integration tests: FastAPI -> mission engine -> queue -> DB (PRD_3 §34)."""

import app.engine.handlers  # noqa: F401
from app.db.models import Mission
from app.engine.executor import execute_mission
from app.engine.queue import InProcessJobQueue
from app.engine.state import MISSION_COMPLETED


async def test_onboard_creates_workspace_and_baseline(api):
    client, db = api["client"], api["db"]
    res = await client.post(
        "/api/v1/team/onboard", json={"url": "https://velocitysports.in", "goal": "increase revenue"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] is True
    assert body["mission_id"]

    mission = await db.get(Mission, body["mission_id"])
    assert mission.status == "QUEUED"
    assert mission.mission_type == "baseline"

    profile_check = await client.post("/api/v1/team/onboard", json={"url": "https://velocitysports.in"})
    assert profile_check.json()["created"] is False
    assert profile_check.json()["merchant_id"] == body["merchant_id"]


async def test_onboard_rejects_bad_url(api):
    res = await api["client"].post("/api/v1/team/onboard", json={"url": "not a url"})
    assert res.status_code == 400


async def test_create_mission_validates_type(api):
    client = api["client"]
    onboard = (await client.post("/api/v1/team/onboard", json={"url": "https://shop.example.com"})).json()

    res = await client.post(
        "/api/v1/team/missions",
        json={
            "merchant_id": onboard["merchant_id"],
            "name": "X",
            "objective": "test objective",
            "mission_type": "nope",
        },
    )
    assert res.status_code == 400


async def test_mission_lifecycle_with_idempotency(api):
    client, sf = api["client"], api["session_factory"]
    onboard = (
        await client.post("/api/v1/team/onboard", json={"url": "https://lifecycle.example.com"})
    ).json()

    payload = {
        "merchant_id": onboard["merchant_id"],
        "name": "Lifecycle probe",
        "objective": "verify create->queue->run->complete",
        "mission_type": "stub",
    }
    headers = {"Idempotency-Key": "life-1"}
    r1 = await client.post("/api/v1/team/missions", json=payload, headers=headers)
    assert r1.status_code == 200
    r2 = await client.post("/api/v1/team/missions", json=payload, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    assert r1.json()["mission_id"] == r2.json()["mission_id"]

    queue = InProcessJobQueue()
    try:
        status = await execute_mission(r1.json()["mission_id"], queue, "w-test", session_factory=sf)
    finally:
        await queue.close()
    assert status == MISSION_COMPLETED

    detail = await client.get(f"/api/v1/team/missions/{r1.json()['mission_id']}")
    assert detail.json()["status"] == "COMPLETED"
    assert detail.json()["result_summary_json"]["steps"] == 3


async def test_cancel_after_completion_conflicts(api):
    client, sf = api["client"], api["session_factory"]
    onboard = (await client.post("/api/v1/team/onboard", json={"url": "https://cancel.example.com"})).json()
    m = (
        await client.post(
            "/api/v1/team/missions",
            json={
                "merchant_id": onboard["merchant_id"],
                "name": "C",
                "objective": "cancel conflict",
                "mission_type": "stub",
            },
        )
    ).json()

    queue = InProcessJobQueue()
    try:
        await execute_mission(m["mission_id"], queue, "w", session_factory=sf)
    finally:
        await queue.close()

    res = await client.post(f"/api/v1/team/missions/{m['mission_id']}/cancel")
    assert res.status_code == 409


async def test_meta_reports_driver_and_limits(api):
    res = await api["client"].get("/api/v1/team/meta")
    body = res.json()
    assert body["queue_driver"] == "in-process"
    assert body["limits"]["max_sub_agent_depth"] >= 1
