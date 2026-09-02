"""Config-driven concurrency limits (PRD_3 §23.10).

Redis-backed counters when REDIS_URL is set (distributed-safe); an asyncio-
locked local implementation otherwise (single-process dev/tests only).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import get_settings


class ConcurrencyLimiter:
    """Counts concurrent units per scope ('global', 'merchant:<id>')."""

    def __init__(self) -> None:
        self._redis = None
        self._local: dict[str, int] = {}
        self._lock = asyncio.Lock()
        url = get_settings().redis_url
        if url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(url, decode_responses=True)

    async def _get(self, key: str) -> int:
        if self._redis is not None:
            value = await self._redis.get(key)
            return int(value or 0)
        async with self._lock:
            return self._local.get(key, 0)

    async def try_acquire(self, key: str, limit: int, ttl_seconds: int = 3600) -> bool:
        """Increment atomically unless already at limit."""
        if self._redis is not None:
            count = await self._redis.incr(key)
            await self._redis.expire(key, ttl_seconds)
            if count > limit:
                await self._redis.decr(key)
                return False
            return True
        async with self._lock:
            if self._local.get(key, 0) >= limit:
                return False
            self._local[key] = self._local.get(key, 0) + 1
            return True

    async def acquire_blocking(self, key: str, limit: int, poll_seconds: float = 0.5) -> None:
        while not await self.try_acquire(key, limit):
            await asyncio.sleep(poll_seconds)

    async def release(self, key: str) -> None:
        if self._redis is not None:
            count = await self._redis.decr(key)
            if count <= 0:
                await self._redis.delete(key)
            return
        async with self._lock:
            current = self._local.get(key, 0)
            if current <= 1:
                self._local.pop(key, None)
            else:
                self._local[key] = current - 1

    @asynccontextmanager
    async def acquire_context(self, key: str, limit: int, ttl_seconds: int = 3600) -> AsyncIterator[None]:
        """Acquire a slot for the duration of an async block (backpressure)."""
        while not await self.try_acquire(key, limit, ttl_seconds):
            await asyncio.sleep(0.25)
        try:
            yield
        finally:
            await self.release(key)


_mission_limiter: ConcurrencyLimiter | None = None
_llm_limiter: ConcurrencyLimiter | None = None


def mission_limiter() -> ConcurrencyLimiter:
    global _mission_limiter
    if _mission_limiter is None:
        _mission_limiter = ConcurrencyLimiter()
    return _mission_limiter


def llm_limiter() -> ConcurrencyLimiter:
    global _llm_limiter
    if _llm_limiter is None:
        _llm_limiter = ConcurrencyLimiter()
    return _llm_limiter
