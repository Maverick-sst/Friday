"""Distributed-safe mission job queue (PRD_3 §22/§23.6).

One interface, two drivers:

- RedisJobQueue: production path. Atomic claim via Lua, lease with heartbeat,
  crash recovery (expired leases return to the ready list), cancellation set.
- InProcessJobQueue: single-process fallback for dev-without-Redis and tests.

PostgreSQL stays the durable source of truth; the queue only coordinates work.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.core.config import get_settings


@dataclass(slots=True)
class Job:
    mission_id: str
    attempt: int = 1


class JobQueue(ABC):
    """Contract shared by both drivers. All methods are async and idempotent."""

    @abstractmethod
    async def enqueue(self, mission_id: str) -> None: ...

    @abstractmethod
    async def claim(self, worker_id: str) -> Job | None:
        """Atomically take the next runnable job and hold a lease on it."""

    @abstractmethod
    async def heartbeat(self, mission_id: str, worker_id: str) -> bool:
        """Extend the lease if we still own it."""

    @abstractmethod
    async def complete(self, mission_id: str, worker_id: str) -> None:
        """Release all coordination state for a finished job."""

    @abstractmethod
    async def release(self, mission_id: str, worker_id: str, *, requeue: bool = True) -> None:
        """Give up the lease; optionally push the job back for another attempt."""

    @abstractmethod
    async def request_cancel(self, mission_id: str) -> None: ...

    @abstractmethod
    async def is_cancel_requested(self, mission_id: str) -> bool: ...

    @abstractmethod
    async def clear_cancel(self, mission_id: str) -> None: ...

    @abstractmethod
    async def depth(self) -> int: ...

    @abstractmethod
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Redis driver
# ---------------------------------------------------------------------------

_CLAIM_LUA = """
-- 1. Recover leases whose expiry passed (crashed workers).
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, m in ipairs(expired) do
  redis.call('LPUSH', KEYS[1], m)
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])

-- 2. Pop candidates until one is claimable or the queue empties.
while true do
  local m = redis.call('RPOP', KEYS[1])
  if not m then return false end
  if redis.call('SISMEMBER', KEYS[3], m) == 1 then
    -- Cancelled while queued: consume the signal and drop the job.
    redis.call('SREM', KEYS[3], m)
  else
    redis.call('ZADD', KEYS[2], ARGV[2], m)
    return m
  end
end
"""

_HEARTBEAT_LUA = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], 'GT', 'XX', ARGV[2], ARGV[1])
  return 1
end
return 0
"""


class RedisJobQueue(JobQueue):
    def __init__(self, redis_url: str):
        settings = get_settings()
        self._redis: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=5)
        self._ready = "acg:missions:ready"
        self._leased = "acg:missions:leased"
        self._cancel = "acg:missions:cancel"
        self._lease_seconds = settings.job_lease_seconds
        self._heartbeat_seconds = settings.job_heartbeat_seconds

    def _keyspace(self) -> tuple[str, str, str]:
        return self._ready, self._leased, self._cancel

    async def enqueue(self, mission_id: str) -> None:
        await self._redis.lpush(self._ready, mission_id)

    async def claim(self, worker_id: str) -> Job | None:
        ready, leased, cancel = self._keyspace()
        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + self._lease_seconds * 1000
        result = await self._redis.eval(_CLAIM_LUA, 3, ready, leased, cancel, now_ms, expiry_ms)
        if result is None or result is False:
            return None
        # Attempt counting happens in Postgres (agent_runs.retry_count); the
        # queue only needs to know it handed the job out.
        return Job(mission_id=str(result))

    async def heartbeat(self, mission_id: str, worker_id: str) -> bool:
        leased = self._leased
        expiry_ms = int(time.time() * 1000) + self._heartbeat_seconds * 1000 * 2
        result = await self._redis.eval(_HEARTBEAT_LUA, 1, leased, mission_id, expiry_ms)
        return bool(result)

    async def complete(self, mission_id: str, worker_id: str) -> None:
        await self._redis.zrem(self._leased, mission_id)
        await self._redis.srem(self._cancel, mission_id)

    async def release(self, mission_id: str, worker_id: str, *, requeue: bool = True) -> None:
        await self._redis.zrem(self._leased, mission_id)
        if requeue:
            await self._redis.lpush(self._ready, mission_id)

    async def request_cancel(self, mission_id: str) -> None:
        await self._redis.sadd(self._cancel, mission_id)

    async def is_cancel_requested(self, mission_id: str) -> bool:
        return bool(await self._redis.sismember(self._cancel, mission_id))

    async def clear_cancel(self, mission_id: str) -> None:
        await self._redis.srem(self._cancel, mission_id)

    async def depth(self) -> int:
        ready, leased, _ = self._keyspace()
        return int(await self._redis.llen(ready)) + int(await self._redis.zcard(leased))

    async def close(self) -> None:
        await self._redis.aclose()


# ---------------------------------------------------------------------------
# In-process driver (dev without Redis / unit tests). NOT distributed-safe by
# design; documented as such so nobody mistakes it for the production path.
# ---------------------------------------------------------------------------


class InProcessJobQueue(JobQueue):
    def __init__(self, maxsize: int = 4096):
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._cancelled: set[str] = set()
        self._in_flight: set[str] = set()

    async def enqueue(self, mission_id: str) -> None:
        if mission_id in self._cancelled:
            self._cancelled.discard(mission_id)
            return
        self._queue.put_nowait(mission_id)

    async def claim(self, worker_id: str) -> Job | None:
        try:
            mission_id = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if mission_id in self._cancelled:
            self._cancelled.discard(mission_id)
            return None
        self._in_flight.add(mission_id)
        return Job(mission_id=mission_id)

    async def heartbeat(self, mission_id: str, worker_id: str) -> bool:
        return mission_id in self._in_flight

    async def complete(self, mission_id: str, worker_id: str) -> None:
        self._in_flight.discard(mission_id)
        self._cancelled.discard(mission_id)

    async def release(self, mission_id: str, worker_id: str, *, requeue: bool = True) -> None:
        self._in_flight.discard(mission_id)
        if requeue and mission_id not in self._cancelled:
            self._queue.put_nowait(mission_id)

    async def request_cancel(self, mission_id: str) -> None:
        self._cancelled.add(mission_id)

    async def is_cancel_requested(self, mission_id: str) -> bool:
        return mission_id in self._cancelled

    async def clear_cancel(self, mission_id: str) -> None:
        self._cancelled.discard(mission_id)

    async def depth(self) -> int:
        return self._queue.qsize()

    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None


def build_queue(redis_url: str | None = None) -> JobQueue:
    """Return the process-wide queue singleton.

    The in-process driver MUST be shared between producers (API) and consumers
    (embedded worker); a fresh instance per call would silently fork the queue.
    The Redis driver is stateless server-side but sharing one connection pool
    is cheaper anyway.
    """
    global _shared_queue
    if _shared_queue is not None:
        return _shared_queue
    url = redis_url if redis_url is not None else get_settings().redis_url
    _shared_queue = RedisJobQueue(url) if url else InProcessJobQueue()
    return _shared_queue


_shared_queue: JobQueue | None = None
