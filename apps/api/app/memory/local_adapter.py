"""Local Postgres-backed memory fallback (used until Mem0 keys are attached).

Keyword-overlap scoring over MemoryRef previews — deliberately simple; the
interface is identical to Mem0Adapter so the provider swap is a config change.
"""

from sqlalchemy import delete, select

from app.db.models import MemoryRef
from app.memory.interface import MemoryHit, MemoryStore, MemoryWriteResult


def _session_factory():
    from app.db.session import AsyncSessionLocal

    return AsyncSessionLocal


class LocalMemoryAdapter(MemoryStore):
    def __init__(self) -> None:
        self._factory = _session_factory()

    async def add(
        self,
        merchant_id: str,
        text: str,
        *,
        kind: str = "observation",
        mission_id: str | None = None,
        metadata: dict | None = None,
    ) -> MemoryWriteResult:
        from app.observability import observation

        with observation(name="memory.write", as_type="span", memory_provider="local", query_type=kind):
            ref = MemoryRef(
                merchant_id=merchant_id,
                provider="local",
                provider_memory_id=f"local-{merchant_id}-{abs(hash((text, kind))) % 10**12}",
                kind=kind,
                text_preview=text[:2000],
                mission_id=mission_id,
                meta_json=metadata or {},
            )
            async with self._factory() as db:
                db.add(ref)
                await db.commit()
            return MemoryWriteResult(accepted=True, provider_memory_ids=[ref.provider_memory_id])

    async def search(self, merchant_id: str, query: str, *, k: int = 5) -> list[MemoryHit]:
        from app.db.base import as_utc
        from app.observability import observation

        with observation(name="memory.read", as_type="span", memory_provider="local", query_type="search"):
            q_words = set(query.lower().split())
            async with self._factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(MemoryRef)
                            .where(MemoryRef.merchant_id == merchant_id)
                            .order_by(MemoryRef.created_at.desc())
                            .limit(500)
                        )
                    )
                    .scalars()
                    .all()
                )
            hits: list[MemoryHit] = []
            for row in rows:
                text = (row.text_preview or "").lower()
                overlap = sum(1 for w in q_words if w in text)
                recency_days = max(0.0, (as_utc(row.created_at).timestamp()))
                # score = keyword overlap + mild recency bonus (bounded 0..1)
                score = min(1.0, overlap / max(len(q_words), 1) * 0.8 + min(recency_days / 10**9, 0.2))
                hits.append(
                    MemoryHit(
                        id=row.provider_memory_id,
                        text=row.text_preview or "",
                        score=score,
                        metadata={"kind": row.kind},
                    )
                )
            hits.sort(key=lambda h: h.score, reverse=True)
            return hits[:k]

    async def clear_merchant(self, merchant_id: str) -> None:
        async with self._factory() as db:
            await db.execute(delete(MemoryRef).where(MemoryRef.merchant_id == merchant_id))
            await db.commit()

    async def close(self) -> None:
        return None
