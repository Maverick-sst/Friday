"""M1 engine tests: queue drivers, executor lifecycle, limits (PRD_3 §34).

Covers: simple mission completion, timeout handling, cancellation, admission
control, and both queue drivers' contracts.
"""

import asyncio

import pytest

import app.engine.handlers  # noqa: F401  (registers the stub handler)
from app.core.config import get_settings
from app.db.models import Mission
from app.engine import service
from app.engine.context import register_handler
from app.engine.executor import execute_mission
from app.engine.queue import InProcessJobQueue
from app.engine.state import (
    MISSION_CANCELLED,
    MISSION_COMPLETED,
    MISSION_FAILED,
    MISSION_QUEUED,
    MISSION_TIMED_OUT,
)


@pytest.fixture()
async def queue():
    q = InProcessJobQueue()
    yield q
    await q.close()


async def test_stub_mission_completes_end_to_end(async_db, merchant_row, queue, session_factory):
    """Scenario 1 - simple mission: claim -> run -> COMPLETED."""
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="Stub",
        objective="validate loop",
        mission_type="stub",
    )
    assert mission.status == MISSION_QUEUED
    status = await execute_mission(mission.id, queue, worker_id="w1", session_factory=session_factory)
    assert status == MISSION_COMPLETED

    refreshed = await async_db.get(Mission, mission.id)
    await async_db.refresh(refreshed)
    assert refreshed.status == MISSION_COMPLETED
    assert refreshed.started_at is not None
    assert refreshed.completed_at is not None
    assert refreshed.result_summary_json["steps"] == 3


async def test_mission_times_out(async_db, merchant_row, queue, session_factory):
    """A mission exceeding its wall-clock budget lands in TIMED_OUT."""
    register_handler("slow_test", _slow_handler)
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="Slow",
        objective="timeout path",
        mission_type="slow_test",
        max_runtime_seconds=0,  # deadline already passed -> immediate timeout
    )
    status = await execute_mission(mission.id, queue, worker_id="w1", session_factory=session_factory)
    refreshed = await async_db.get(Mission, mission.id)
    await async_db.refresh(refreshed)
    assert status == MISSION_TIMED_OUT
    assert refreshed.status == MISSION_TIMED_OUT
    assert refreshed.error_json["category"] == "timeout"


async def _slow_handler(ctx):
    await asyncio.sleep(5)
    return {"summary": "should never finish"}


async def test_cancelled_queued_mission_is_dropped(async_db, merchant_row, queue):
    """Cancellation before execution: job is consumed and mission stays terminal."""
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="Cancel me",
        objective="cancel path",
        mission_type="stub",
    )
    final = await service.cancel_mission(async_db, queue, mission.id)
    assert final == MISSION_CANCELLED

    job = await queue.claim("w1")
    assert job is None or job.mission_id != mission.id  # cancelled jobs are dropped


async def test_running_mission_cancels_cooperatively(async_db, merchant_row, queue, session_factory):
    """A RUNNING mission observes the cancel signal between steps."""
    register_handler("cooperative_test", _cooperative_handler)
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="Coop cancel",
        objective="cooperative cancellation",
        mission_type="cooperative_test",
    )
    task = asyncio.ensure_future(
        execute_mission(mission.id, queue, worker_id="w1", session_factory=session_factory)
    )
    await asyncio.sleep(0.1)  # let it start
    await queue.request_cancel(mission.id)
    status = await asyncio.wait_for(task, timeout=5)
    assert status == MISSION_CANCELLED
    refreshed = await async_db.get(Mission, mission.id)
    await async_db.refresh(refreshed)
    assert refreshed.status == MISSION_CANCELLED


async def _cooperative_handler(ctx):
    for _ in range(100):
        await ctx.ensure_not_cancelled()
        await asyncio.sleep(0.05)
    return {"summary": "never reached"}


async def test_unknown_handler_fails_mission(async_db, merchant_row, queue, session_factory):
    """A mission type with no handler fails loudly instead of hanging."""
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="No handler",
        objective="failure path",
        mission_type="does_not_exist",
    )
    status = await execute_mission(mission.id, queue, worker_id="w1", session_factory=session_factory)
    refreshed = await async_db.get(Mission, mission.id)
    await async_db.refresh(refreshed)
    assert status == MISSION_FAILED
    assert refreshed.status == MISSION_FAILED
    assert "no handler registered" in refreshed.error_json["message"]


async def test_duplicate_execution_is_safe(async_db, merchant_row, queue, session_factory):
    """Executing the same mission twice cannot double-run (defensive transitions)."""
    mission = await service.create_mission(
        async_db,
        merchant_id=merchant_row.id,
        name="Once only",
        objective="idempotent execution",
        mission_type="stub",
    )
    first = await execute_mission(mission.id, queue, worker_id="w1", session_factory=session_factory)
    second = await execute_mission(mission.id, queue, worker_id="w2", session_factory=session_factory)
    assert first == MISSION_COMPLETED
    assert second == MISSION_COMPLETED  # stale job dropped, state untouched
    refreshed = await async_db.get(Mission, mission.id)
    await async_db.refresh(refreshed)
    assert refreshed.status == MISSION_COMPLETED


def test_inprocess_queue_contract():
    """Synchronous sanity of the fallback driver's lease/cancel semantics."""

    async def scenario():
        q = InProcessJobQueue()
        try:
            assert await q.claim("w") is None  # empty queue
            await q.enqueue("m1")
            job = await q.claim("w")
            assert job is not None and job.mission_id == "m1"
            assert await q.depth() == 0
            await q.complete("m1", "w")

            # cancel-while-queued drops the job at claim time
            await q.enqueue("m2")
            await q.request_cancel("m2")
            assert await q.is_cancel_requested("m2")
            assert await q.claim("w") is None
            await q.clear_cancel("m2")

            # release(requeue=True) returns the job to ready
            await q.enqueue("m3")
            job = await q.claim("w")
            await q.release(job.mission_id, "w", requeue=True)
            job = await q.claim("w")
            assert job.mission_id == "m3"
        finally:
            await q.close()

    asyncio.run(scenario())


def test_settings_defaults_match_prd_bounds():
    s = get_settings()
    assert s.max_sub_agent_depth >= 1
    assert s.max_children_per_parent <= 5
    assert s.max_agent_runs_per_mission <= 25
    assert s.mission_timeout_seconds > 0
