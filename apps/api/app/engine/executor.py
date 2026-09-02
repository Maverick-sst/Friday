"""Mission executor: drives one claimed mission to a terminal state.

State-transition discipline (PRD_3 §23.4/§26):
- Every transition is a conditional UPDATE ... WHERE status IN (expected),
  so duplicate workers or retries can never corrupt mission state.
- Timeouts, cancellation, and budget exhaustion all end in explicit terminal
  states; no mission remains RUNNING indefinitely.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from app.core.config import get_settings
from app.db.models import Mission, UsageEvent
from app.db.session import AsyncSessionLocal
from app.engine.context import MissionCancelled, MissionContext, get_handler
from app.engine.limits import mission_limiter
from app.engine.progress import ProgressEvent, progress_bus
from app.engine.queue import JobQueue
from app.engine.state import (
    MISSION_CANCELLED,
    MISSION_COMPLETED,
    MISSION_FAILED,
    MISSION_QUEUED,
    MISSION_RUNNING,
    MISSION_TIMED_OUT,
)

logger = logging.getLogger("acg.engine.executor")

_CANCEL_POLL_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _transition(db, mission_id: str, expected: set[str], target: str, **fields: Any) -> bool:
    result = await db.execute(
        update(Mission)
        .where(Mission.id == mission_id, Mission.status.in_(expected))
        .values(status=target, **fields)
    )
    return bool(result.rowcount)


async def _finish(
    db,
    mission_id: str,
    status: str,
    *,
    result_summary: dict | None = None,
    error: dict | None = None,
    completed_at: datetime,
) -> None:
    values: dict[str, Any] = {"status": status, "completed_at": completed_at}
    if result_summary is not None:
        values["result_summary_json"] = result_summary
    if error is not None:
        values["error_json"] = error
    await db.execute(
        update(Mission).where(Mission.id == mission_id, Mission.status == MISSION_RUNNING).values(**values)
    )


async def execute_mission(
    mission_id: str,
    queue: JobQueue,
    worker_id: str,
    *,
    session_factory=None,
) -> str:
    """Run one claimed mission to a terminal state. Returns the final status."""
    settings = get_settings()
    limiter = mission_limiter()
    session_factory = session_factory or AsyncSessionLocal

    async with session_factory() as db:
        mission = await db.get(Mission, mission_id)
        if mission is None:
            await queue.complete(mission_id, worker_id)
            return "MISSING"
        if mission.status != MISSION_QUEUED:
            # Terminal or already running elsewhere: drop the stale job.
            await queue.complete(mission_id, worker_id)
            return mission.status

        merchant_key = f"acg:conc:merchant:{mission.merchant_id}"

        # Admission control (PRD_3 §23.10): global + per-merchant caps.
        if not await limiter.try_acquire("acg:conc:global", settings.max_concurrent_missions_global):
            await queue.release(mission_id, worker_id, requeue=True)
            return MISSION_QUEUED
        if not await limiter.try_acquire(merchant_key, settings.max_concurrent_missions_per_merchant):
            await limiter.release("acg:conc:global")
            await queue.release(mission_id, worker_id, requeue=True)
            return MISSION_QUEUED

        try:
            ok = await _transition(db, mission_id, {MISSION_QUEUED}, MISSION_RUNNING, started_at=_utcnow())
            await db.commit()
            if not ok:
                return mission.status  # lost the race; nothing to do

            async def _publish(kind: str, **payload: Any) -> None:
                await progress_bus().publish(ProgressEvent(mission_id=mission_id, kind=kind, payload=payload))

            # --- Observability: mission root trace (OTEL_LANGFUSE_EXECUTION_PRD §9.1/§26) ---
            from app.observability import observation, propagate_attributes

            payload_ctx = {
                "mission_id": mission_id,
                "merchant_id": mission.merchant_id,
                "mission_type": mission.mission_type,
                "objective": (mission.objective or "")[:400],
            }
            propagate_attributes(**payload_ctx)

            await _publish("mission_status", status="RUNNING", objective=mission.objective[:200])

            ctx = MissionContext.from_mission_row(mission)
            ctx.queue = queue
            ctx.artifacts["agent_assignments"] = list(getattr(mission, "agent_assignments_json", []) or [])
            ctx.deadline = time.monotonic() + min(
                mission.max_runtime_seconds, settings.mission_timeout_seconds
            )

            async def _check_cancel() -> bool:
                return await queue.is_cancel_requested(mission_id)

            ctx.check_cancel = _check_cancel

            try:
                handler = get_handler(mission.mission_type)
                if handler is None:
                    raise LookupError(f"no handler registered for mission_type={mission.mission_type!r}")
                # Mission-root observation (PRD 9.1): all agent/tool/LLM spans
                # nest beneath this via OTel context propagation.
                from app.observability import observation

                with observation(name=f"mission.{mission.mission_type}", as_type="span") as _mission_span:
                    result = await asyncio.wait_for(
                        _run_with_cancellation_checks(handler, ctx),
                        timeout=max(ctx.remaining_seconds() or 0.001, 0.001),
                    )
                    if _mission_span is not None:
                        try:
                            _mission_span.update(
                                status=str(result.get("_status", MISSION_COMPLETED))[:50]
                                if isinstance(result, dict)
                                else MISSION_COMPLETED
                            )
                        except Exception:
                            pass
                final_status = str(result.pop("_status", MISSION_COMPLETED))
                await _finish(db, mission_id, final_status, result_summary=result, completed_at=_utcnow())
                status = final_status
                await _publish("mission_status", status=final_status)
            except asyncio.TimeoutError:
                status = MISSION_TIMED_OUT
                await _finish(
                    db,
                    mission_id,
                    MISSION_TIMED_OUT,
                    error={"category": "timeout", "message": "mission deadline exceeded"},
                    completed_at=_utcnow(),
                )
                await _publish("mission_status", status=MISSION_TIMED_OUT)
            except MissionCancelled:
                status = MISSION_CANCELLED
                await _finish(
                    db,
                    mission_id,
                    MISSION_CANCELLED,
                    error={"category": "cancelled", "message": "cancellation requested"},
                    completed_at=_utcnow(),
                )
                await _publish("mission_status", status=MISSION_CANCELLED)
            except Exception as exc:
                logger.exception("mission %s failed", mission_id)
                status = MISSION_FAILED
                await _finish(
                    db,
                    mission_id,
                    MISSION_FAILED,
                    error={"category": "exception", "message": str(exc)[:500]},
                    completed_at=_utcnow(),
                )
                await _publish("mission_status", status=MISSION_FAILED)
        finally:
            await limiter.release("acg:conc:global")
            await limiter.release(merchant_key)
            await queue.complete(mission_id, worker_id)

        await db.execute(
            UsageEvent.__table__.insert().values(
                merchant_id=mission.merchant_id,
                mission_id=mission_id,
                kind="mission_completed",
                meta_json={"status": status},
            )
        )
        await db.commit()
        return status


async def _run_with_cancellation_checks(handler, ctx: MissionContext):
    """Run the handler while polling the cancellation signal between steps."""
    task = asyncio.ensure_future(handler(ctx))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_CANCEL_POLL_SECONDS)
            if task in done:
                return task.result()
            if await ctx.cancelled():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise asyncio.CancelledError()
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise
