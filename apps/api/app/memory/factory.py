"""Memory store factory (PRD_3 §14: provider-agnostic behind one interface)."""

from functools import lru_cache

from app.core.config import get_settings
from app.memory.interface import MemoryStore


@lru_cache
def get_memory_store() -> MemoryStore:
    settings = get_settings()
    if settings.mem0_ready:
        from app.memory.mem0_adapter import Mem0Adapter

        return Mem0Adapter()
    from app.memory.local_adapter import LocalMemoryAdapter

    return LocalMemoryAdapter()


@lru_cache
def memory_provider_name() -> str:
    return "mem0" if get_settings().mem0_ready else "local"
