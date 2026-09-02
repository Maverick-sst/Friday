"""Mission-type handler registry.

Handlers receive a MissionContext and return a JSON-safe result dict that
lands in Mission.result_summary_json. A special "_status" key overrides the
final mission status (e.g. PARTIALLY_COMPLETED).

M1 ships only the `stub` type used to validate the execution loop; specialist
agents and the baseline graph register their handlers in later milestones.
"""

import asyncio
import logging
import time

from app.engine.context import MissionContext, register_handler
from app.engine.progress import ProgressEvent, progress_bus
from app.engine.state import MISSION_COMPLETED

logger = logging.getLogger("acg.engine.handlers")


async def _stub_handler(ctx: MissionContext) -> dict:
    started = time.monotonic()
    steps = 3
    for step in range(steps):
        await ctx.ensure_not_cancelled()
        remaining = ctx.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise asyncio.TimeoutError()
        await progress_bus().publish(
            ProgressEvent(
                mission_id=ctx.mission_id,
                kind="log",
                payload={"label": f"stub step {step + 1}/{steps}", "step": step + 1},
            )
        )
        await asyncio.sleep(0.05)
    return {
        "summary": "Stub handler completed all steps.",
        "steps": steps,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "_status": MISSION_COMPLETED,
    }


register_handler("stub", _stub_handler)
