"""Worker process: claims missions from the queue and executes them.

Distributed-safe (PRD_3 §23.6): no correctness-relevant process-local state;
run N replicas of `python -m app.engine.worker` safely. Crashed workers'
leases expire and the queue hands the job to a survivor.

Usage:
    python -m app.engine.worker            # runs until SIGINT/SIGTERM
"""

import asyncio
import logging
import signal
import uuid
from contextlib import nullcontext

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.engine import (
    executor,
    handlers,  # noqa: F401  (registers the stub mission handler)
)
from app.engine.context import registered_types
from app.engine.queue import JobQueue, build_queue
from app.intel.experiments import register_experiment_handler
from app.intel.handlers import register_all as _register_intel_handlers

_register_intel_handlers()
register_experiment_handler()

logger = logging.getLogger("acg.engine.worker")


async def run_worker(stop: asyncio.Event) -> None:
    settings = get_settings()
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    queue: JobQueue = build_queue()
    logger.info(
        "worker %s starting (driver=%s concurrency=%d handlers=%s)",
        worker_id,
        type(queue).__name__,
        settings.worker_concurrency,
        ",".join(registered_types()) or "none",
    )

    in_flight: set[asyncio.Task] = set()

    async def _supervised(mission_id: str) -> None:
        try:
            status = await executor.execute_mission(mission_id, queue, worker_id)
            logger.info("mission %s -> %s", mission_id, status)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("mission %s crashed the supervisor", mission_id)

    async def _heartbeat_loop() -> None:
        while not stop.is_set():
            await asyncio.sleep(settings.job_heartbeat_seconds)
            for task in list(in_flight):
                if not task.done():
                    await queue.heartbeat(task.get_name() or "", worker_id)

    heartbeat = asyncio.ensure_future(_heartbeat_loop())
    try:
        while not stop.is_set():
            try:
                # Bounded fan-out: never more than worker_concurrency tasks at once.
                if len(in_flight) >= settings.worker_concurrency:
                    done, _, _ = await asyncio.wait(
                        in_flight, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                    )
                    in_flight -= done
                    continue

                job = await queue.claim(worker_id)
                if job is None:
                    await asyncio.sleep(0.5)
                    continue

                task = asyncio.ensure_future(_supervised(job.mission_id))
                task.set_name(job.mission_id)
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker %s loop error; continuing", worker_id)
                await asyncio.sleep(1.0)

        if in_flight:
            logger.info("draining %d in-flight mission(s)", len(in_flight))
            with nullcontext():
                await asyncio.gather(*in_flight, return_exceptions=True)
    finally:
        heartbeat.cancel()
        await queue.close()
        logger.info("worker %s stopped", worker_id)


def main() -> None:
    configure_logging("INFO")
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()

    def _sig(*_: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig)
        except NotImplementedError:  # pragma: no cover - non-unix dev boxes
            pass

    try:
        loop.run_until_complete(run_worker(stop))
    finally:
        # Observability: flush any buffered traces (PRD 22/40).
        try:
            from app.observability import flush_telemetry

            flush_telemetry()
        except Exception:
            pass
        loop.close()


if __name__ == "__main__":
    main()
