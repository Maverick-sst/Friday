"""Live mission progress events for SSE streaming (PRD_3 §31/§35).

Two backends behind one interface:
- In-process pub/sub (dict of per-mission asyncio.Queue sets) for the
  same-process API/worker case, dev, and tests.
- Redis pub/sub when REDIS_URL is set so workers and the API can live in
  different processes.

Events are transient coordination data; durable history lives in Postgres
(missions / agent_runs / evidence rows), which is what the UI replays when it
loads a finished mission.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger("acg.engine.progress")


@dataclass(slots=True)
class ProgressEvent:
    mission_id: str
    kind: str  # mission_status|run_status|tool_call|evidence|finding|log
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "mission_id": self.mission_id,
                "kind": self.kind,
                "ts": self.ts,
                "payload": self.payload,
            },
            default=str,
        )

    def to_sse(self, seq: int | None = None) -> str:
        # `id:` enables native EventSource reconnect with Last-Event-ID, so a
        # client that reconnects resumes exactly where it left off (Fleet A3).
        id_line = f"id: {seq}\n" if seq is not None else ""
        return (
            id_line
            + "data: "
            + json.dumps(
                {
                    "mission_id": self.mission_id,
                    "kind": self.kind,
                    "ts": self.ts,
                    **self.payload,
                },
                default=str,
            )
            + "\n\n"
        )


class _LocalChannel:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, mission_id: str, event: ProgressEvent) -> None:
        async with self._lock:
            queues = list(self._subs.get(mission_id, ()))
        for q in queues:
            q.put_nowait(event)

    async def subscribe(self, mission_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subs.setdefault(mission_id, set()).add(q)
        return q

    async def unsubscribe(self, mission_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.get(mission_id, set()).discard(q)


class ProgressBus:
    # Replay ring buffer per mission (Fleet PRD A3): a client connecting
    # mid-run receives everything it missed instead of going quiet until the
    # next event. Bounded: REPLAY_MAX events per mission, MAX_BUFFERED_MISSIONS
    # missions (oldest mission buffers evicted first).
    REPLAY_MAX = 300
    MAX_BUFFERED_MISSIONS = 64

    def __init__(self) -> None:
        settings = get_settings()
        self._redis = None
        if settings.redis_url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._local = _LocalChannel()
        self._buf_lock = asyncio.Lock()
        self._buffers: dict[str, deque[tuple[int, ProgressEvent]]] = {}
        self._seqs: dict[str, int] = {}

    async def _record(self, ev: ProgressEvent) -> int:
        """Buffer one event for replay; returns its per-mission sequence id."""
        async with self._buf_lock:
            # Evict oldest mission buffers beyond the cap (oldest by dict order).
            while len(self._buffers) >= self.MAX_BUFFERED_MISSIONS and ev.mission_id not in self._buffers:
                oldest = next(iter(self._buffers))
                self._buffers.pop(oldest, None)
                self._seqs.pop(oldest, None)
            seq = self._seqs.get(ev.mission_id, 0) + 1
            self._seqs[ev.mission_id] = seq
            buf = self._buffers.setdefault(ev.mission_id, deque(maxlen=self.REPLAY_MAX))
            buf.append((seq, ev))
            return seq

    async def replay(self, mission_id: str, after_seq: int = 0) -> list[tuple[int, ProgressEvent]]:
        """Buffered events with seq > after_seq, in order (Fleet PRD A3)."""
        async with self._buf_lock:
            buf = self._buffers.get(mission_id)
            if not buf:
                return []
            return [(s, e) for s, e in buf if s > after_seq]

    async def publish(self, event: ProgressEvent) -> None:
        # Trace<->SSE correlation (OTEL_LANGFUSE_EXECUTION_PRD §14): attach the
        # active trace/span so a live event can link to its Langfuse trace.
        # Best-effort and omitted when tracing is disabled.
        from app.observability import get_span_id, get_trace_id

        ev = event
        try:
            trace_id = get_trace_id()
            span_id = get_span_id()
            if trace_id and span_id:
                ev = ProgressEvent(
                    mission_id=event.mission_id,
                    kind=event.kind,
                    payload={**event.payload, "trace_id": trace_id, "span_id": span_id},
                    ts=event.ts,
                )
        except Exception:
            pass  # correlation is best-effort; never break streaming
        try:
            await self._record(ev)
        except Exception:
            logger.debug("replay record failed", exc_info=True)
        await self._local.publish(ev.mission_id, ev)
        if self._redis is not None:
            try:
                await self._redis.publish(f"acg:progress:{ev.mission_id}", ev.to_json())
            except Exception:
                logger.debug("progress publish failed", exc_info=True)

    async def stream(self, mission_id: str, after_seq: int = 0):
        """Yield (seq, event) pairs: replayed backlog first, then live events.

        `after_seq` comes from the client's Last-Event-ID so a reconnecting
        client resumes without gaps or duplicates. The subscription is taken
        BEFORE the replay snapshot so no event can fall in the gap between
        backlog and live tail; overlap is removed by `_record_if_new` dedup.
        """
        queue = await self._local.subscribe(mission_id)
        redis_task = None
        try:
            if self._redis is not None:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(f"acg:progress:{mission_id}")
                redis_task = asyncio.ensure_future(self._pump_redis(pubsub, queue))
            for seq, ev in await self.replay(mission_id, after_seq):
                yield seq, ev
            while True:
                event: ProgressEvent = await queue.get()
                seq = await self._record_if_new(mission_id, event)
                yield seq, event
        finally:
            if redis_task is not None:
                redis_task.cancel()
            await self._local.unsubscribe(mission_id, queue)

    async def _record_if_new(self, mission_id: str, event: ProgressEvent) -> int:
        """Record a live event, deduping redis-pumped copies of our own publishes.

        In redis mode an event may arrive both via publish() (same process) and
        via the redis pump (cross-process path). Identity: (mission, ts, kind).
        """
        async with self._buf_lock:
            buf = self._buffers.get(mission_id)
            if buf:
                for s, e in reversed(buf):
                    if e.ts == event.ts and e.kind == event.kind:
                        return s
            seq = self._seqs.get(mission_id, 0) + 1
            self._seqs[mission_id] = seq
            buf = self._buffers.setdefault(mission_id, deque(maxlen=self.REPLAY_MAX))
            buf.append((seq, event))
            return seq

    async def _pump_redis(self, pubsub, queue: asyncio.Queue) -> None:
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = json.loads(message["data"])
                event = ProgressEvent(
                    mission_id=data["mission_id"],
                    kind=data["kind"],
                    payload=data.get("payload", {}),
                    ts=data.get("ts", time.time()),
                )
                await queue.put(event)
        except asyncio.CancelledError:
            pass


_bus: ProgressBus | None = None


def progress_bus() -> ProgressBus:
    global _bus
    if _bus is None:
        _bus = ProgressBus()
    return _bus
