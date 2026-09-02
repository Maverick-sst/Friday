"""Database engine and session management."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _to_async_url(url: str) -> str:
    """Map the sync DATABASE_URL to its async driver equivalent."""
    if url.startswith("sqlite+aiosqlite"):
        return url
    if url.startswith("sqlite"):
        return "sqlite+aiosqlite" + url[len("sqlite") :]
    return url  # postgresql+psycopg works for both sync and async (psycopg3)


def make_engine(url: str | None = None):
    return create_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        future=True,
    )


engine = make_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_async_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        url or _to_async_url(settings.database_url),
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        pool_timeout=30,
        future=True,
    )


# Engine used by the strategy-team engine (missions / agents / workers).
# Same declarative models as the sync V0 sessions; separate pool so long-running
# mission work never starves request-scoped connections.
async_engine = make_async_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_async_db() -> Iterator[AsyncSession]:
    async with AsyncSessionLocal() as db:
        yield db
