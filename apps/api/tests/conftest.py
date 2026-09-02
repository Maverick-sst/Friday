"""Shared pytest fixtures."""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import Base


@pytest.fixture(autouse=True)
def _observability_off(monkeypatch):
    """Force tracing OFF for every test / test suite default.

    The docker-compose test container injects real LANGFUSE_* env vars, so
    without this guard the existing mission tests would unexpectedly start
    exporting traces to the Cloud account. Observability tests re-enable it
    with an injected fake client. (OTEL_LANGFUSE_EXECUTION_PRD §34 / §21.)
    """
    import app.observability as obs

    settings = get_settings()
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    obs._override_client(None)  # idempotent; forces _enabled=False
    yield
    obs._override_client(None)


@pytest.fixture(autouse=True)
def _llm_credentials_off(monkeypatch):
    """Force the deterministic ScriptedBrain in tests (hermetic runner).

    The compose test container injects STRATEGY_LLM_* (and possibly AGENT_LLM_*)
    env vars; without this guard `make_brain` would pick the real-LLM brain and
    fire real HTTP calls against the provider during mission tests. Tests that
    want the LLM brain must re-set creds in their own fixture.


    (Same rationale as `_observability_off` above.)
    """
    settings = get_settings()
    for _name in (
        "agent_llm_api_key",
        "agent_llm_base_url",
        "strategy_llm_api_key",
        "strategy_llm_base_url",
    ):
        monkeypatch.setattr(settings, _name, "")
    # B6: same hermetic guard for the cloud browser key — a real key in .env
    # must not make build_plane() return a BrowserUseToolPlane during tests.
    monkeypatch.setattr(settings, "browser_use_api_key", "")
    yield


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest_asyncio.fixture()
async def db_env(tmp_path):
    """Engine + shared assertion session + fresh-session factory.

    A file (not in-memory) sqlite DB + NullPool lets multiple concurrent
    sessions exist as they do against production Postgres; the mission
    executor opens its own sessions per agent run.
    """
    db_path = tmp_path / "engine_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True, poolclass=NullPool)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    session = maker()

    @asynccontextmanager
    async def _factory():
        async with maker() as s:
            yield s

    try:
        yield {"session": session, "factory": _factory}
    finally:
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture()
async def async_db(db_env):
    return db_env["session"]


@pytest_asyncio.fixture()
async def session_factory(db_env):
    """Fresh-session factory bound to the same DB as `async_db`.

    Tests inject this into the executor/handlers so every concurrent agent
    run gets its own session, mirroring production connection semantics.
    """
    return db_env["factory"]


@pytest_asyncio.fixture()
async def merchant_row(async_db):
    from app.db.models import Merchant

    m = Merchant(name="Test Merchant", slug="test-merchant", website_url="https://example.com")
    async_db.add(m)
    await async_db.commit()
    return m


@pytest.fixture()
async def api(monkeypatch):
    """App under test wired to an in-memory sqlite DB; embedded worker off."""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.db.models import Base
    from app.db.session import get_async_db
    from app.main import create_app

    settings = get_settings()
    monkeypatch.setattr(settings, "embedded_worker", False)

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    session = factory()

    async def _override():
        yield session

    app = create_app()
    app.dependency_overrides[get_async_db] = _override

    @asynccontextmanager
    async def _sf():
        yield session

    transport = ASGITransport(app=app)
    try:
        yield {
            "client": AsyncClient(transport=transport, base_url="http://test"),
            "db": session,
            "session_factory": _sf,
        }
    finally:
        await session.close()
        await engine.dispose()
