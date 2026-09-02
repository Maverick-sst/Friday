"""Mem0 platform adapter (live REST; verified against v1 add + v2 search).

Ingestion is asynchronous upstream: `add` returns accepted immediately and the
memory becomes searchable within seconds. Callers should not block on that.
"""

import logging

import httpx

from app.core.config import get_settings
from app.memory.interface import MemoryHit, MemoryStore, MemoryWriteResult

logger = logging.getLogger("acg.memory.mem0")

_BASE = "https://api.mem0.ai"


class Mem0Adapter(MemoryStore):
    def __init__(self) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=_BASE,
            headers={
                "Authorization": f"Token {settings.mem0_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def add(
        self,
        merchant_id: str,
        text: str,
        *,
        kind: str = "observation",
        mission_id: str | None = None,
        metadata: dict | None = None,
    ) -> MemoryWriteResult:
        # user_id is merchant-scoped so all five agents share one memory space
        # per merchant, while different merchants stay fully isolated.
        # --- Observability (PRD 9.5/29): instrument the memory abstraction. ---
        from app.observability import observation

        with observation(name="memory.write", as_type="span", memory_provider="mem0", query_type=kind):
            payload = {
                "messages": [{"role": "user", "content": text}],
                "user_id": f"merchant-{merchant_id}",
                "metadata": {"kind": kind, **(metadata or {})},
            }
            try:
                resp = await self._client.post("/v1/memories/", json=payload)
                if resp.status_code in (200, 201, 202):
                    data = resp.json()
                    ids = [
                        item.get("id")
                        for item in (data if isinstance(data, list) else [])
                        if isinstance(item, dict)
                    ]
                    return MemoryWriteResult(accepted=True, provider_memory_ids=[i for i in ids if i])
                logger.warning("mem0 add %s: %s %s", kind, resp.status_code, resp.text[:200])
                return MemoryWriteResult(accepted=False, note=f"http {resp.status_code}")
            except httpx.HTTPError as exc:
                logger.warning("mem0 add network error: %s", exc)
                return MemoryWriteResult(accepted=False, note=str(exc)[:200])

    async def search(self, merchant_id: str, query: str, *, k: int = 5) -> list[MemoryHit]:
        from app.observability import observation

        with observation(name="memory.read", as_type="span", memory_provider="mem0", query_type="search"):
            try:
                resp = await self._client.post(
                    "/v2/memories/search/",
                    json={"query": query, "filters": {"user_id": f"merchant-{merchant_id}"}, "limit": k},
                )
                if resp.status_code != 200:
                    logger.warning("mem0 search %s: %s", resp.status_code, resp.text[:200])
                    return []
                hits = []
                for item in resp.json() or []:
                    hits.append(
                        MemoryHit(
                            id=item.get("id") or "",
                            text=item.get("memory") or "",
                            score=float(item.get("score") or 0.0),
                            metadata=item.get("metadata") or {},
                        )
                    )
                return hits
            except httpx.HTTPError as exc:
                logger.warning("mem0 search network error: %s", exc)
                return []

    async def close(self) -> None:
        await self._client.aclose()
