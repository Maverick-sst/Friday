"""Mission service: creation, queueing, cancellation (PRD_3 §9/§26).

The API layer calls these functions; all state transitions are defensive so
retries and concurrent submissions cannot duplicate missions (idempotency is
enforced at the API boundary via core.idempotency).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Merchant, Mission, UsageEvent
from app.engine.queue import JobQueue
from app.engine.state import (
    MISSION_CREATED,
    MISSION_QUEUED,
    MISSION_RUNNING,
    MISSION_TERMINAL_STATES,
)

logger = logging.getLogger("acg.engine.service")


async def create_mission(
    db: AsyncSession,
    *,
    merchant_id: str,
    name: str,
    objective: str,
    mission_type: str = "on_demand",
    priority: str = "normal",
    budget_runs: int | None = None,
    max_runtime_seconds: int | None = None,
    agent_assignments: list[str] | None = None,
) -> Mission:
    settings = get_settings()
    merchant = await db.get(Merchant, merchant_id)
    if merchant is None:
        raise LookupError(f"merchant {merchant_id} not found")

    mission = Mission(
        merchant_id=merchant_id,
        name=name[:255],
        objective=objective,
        mission_type=mission_type,
        status=MISSION_QUEUED,
        priority=priority,
        budget_runs=budget_runs if budget_runs is not None else settings.max_agent_runs_per_mission,
        max_runtime_seconds=(
            max_runtime_seconds if max_runtime_seconds is not None else settings.mission_timeout_seconds
        ),
        agent_assignments_json=agent_assignments or [],
    )
    db.add(mission)
    await db.flush()
    db.add(
        UsageEvent(
            merchant_id=merchant_id,
            mission_id=mission.id,
            kind="mission_created",
            meta_json={"mission_type": mission_type, "priority": priority},
        )
    )
    mission.status = MISSION_QUEUED
    await db.commit()
    return mission


async def enqueue_mission(queue: JobQueue, mission: Mission) -> None:
    """Push a queued mission onto the queue; makes it visible to workers."""
    if mission.status != MISSION_QUEUED:
        return
    await queue.enqueue(mission.id)


async def cancel_mission(db: AsyncSession, queue: JobQueue, mission_id: str) -> str:
    """Cancel a queued or running mission. Idempotent."""
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise LookupError(f"mission {mission_id} not found")
    if mission.status in MISSION_TERMINAL_STATES:
        return mission.status
    await queue.request_cancel(mission_id)
    if mission.status in {MISSION_CREATED, MISSION_QUEUED}:
        mission.status = "CANCELLED"
        await db.commit()
    # RUNNING missions observe the cancel signal cooperatively in the executor.
    return "CANCELLED"


async def get_mission_or_none(db: AsyncSession, mission_id: str) -> Mission | None:
    return await db.get(Mission, mission_id)


async def list_missions(
    db: AsyncSession,
    *,
    merchant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Mission]:
    stmt = select(Mission).where(Mission.merchant_id == merchant_id)
    if status:
        stmt = stmt.where(Mission.status == status)
    stmt = stmt.order_by(Mission.created_at.desc()).limit(limit).offset(offset)
    return list((await db.scalars(stmt)).all())


async def active_run_count(db: AsyncSession, *, merchant_id: str) -> int:
    stmt = (
        select(Mission)
        .where(Mission.merchant_id == merchant_id, Mission.status == MISSION_RUNNING)
        .limit(1000)
    )
    return len(list((await db.scalars(stmt)).all()))
