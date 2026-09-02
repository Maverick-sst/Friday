"""Persistent semantic memory for the AI team (PRD_3 §14).

PostgreSQL stays the structured source of truth; this layer is durable
*semantic* memory only (goals, stable facts, learned context). Providers are
swappable behind MemoryStore; every stored item should also land a MemoryRef
row in Postgres for audit (callers do that, not providers).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class MemoryHit:
    id: str
    text: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class MemoryWriteResult:
    accepted: bool
    provider_memory_ids: list[str] = field(default_factory=list)
    note: str | None = None


class MemoryStore(ABC):
    """Merchant-scoped key-value semantic memory."""

    @abstractmethod
    async def add(
        self,
        merchant_id: str,
        text: str,
        *,
        kind: str = "observation",
        mission_id: str | None = None,
        metadata: dict | None = None,
    ) -> MemoryWriteResult:
        """Store a durable fact/observation. Ingestion may be async upstream."""

    @abstractmethod
    async def search(self, merchant_id: str, query: str, *, k: int = 5) -> list[MemoryHit]:
        """Retrieve the k most relevant memories for a merchant."""

    @abstractmethod
    async def close(self) -> None: ...
