"""Agent Commerce Gateway API entrypoint."""

import logging
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapters import payment_provider_name
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("acg.main")

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id to every inbound request and echoes it back."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            return response
        finally:
            request_id_ctx.reset(token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env == "development":
        # Dev convenience: guarantee schema + demo merchant exist.
        from sqlalchemy import select

        from app.db.models import Base, Merchant
        from app.db.seeds import seed_mock_merchant
        from app.db.session import SessionLocal, engine

        try:
            if settings.database_url.startswith("sqlite"):
                Base.metadata.create_all(engine)

            with SessionLocal() as db:
                existing = db.scalar(select(Merchant).where(Merchant.slug == "velocity-sports"))
                if existing is None:
                    seed_mock_merchant(db)
                    logger.info("dev autoseed created mock merchant")
        except Exception as exc:
            logger.warning(f"dev autoseed skipped: {exc}")

    # Embedded strategy-team workers (single-process demo mode, PRD_3 §23.2).
    # A supervisor restarts the worker if it ever dies unexpectedly.
    worker_task = None
    worker_stop = None
    if settings.embedded_worker:
        import asyncio

        from app.engine import handlers  # noqa: F401  (register stub handler)
        from app.intel.experiments import register_experiment_handler
        from app.intel.handlers import register_all as register_intel

        register_intel()
        register_experiment_handler()
        from app.engine.worker import run_worker

        async def _supervise() -> None:
            restarts = 0
            while not worker_stop.is_set():
                try:
                    await run_worker(worker_stop)
                    break  # clean stop requested
                except asyncio.CancelledError:
                    raise
                except Exception:
                    restarts += 1
                    logger.exception("embedded worker crashed (restart #%d)", restarts)
                    await asyncio.sleep(2.0)

        worker_stop = asyncio.Event()
        worker_task = asyncio.ensure_future(_supervise())
        logger.info("embedded strategy-team supervisor started")

    yield

    if worker_task is not None and worker_stop is not None:
        worker_stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except Exception:
            worker_task.cancel()
        logger.info("embedded strategy-team supervisor stopped")

    # Observability: flush buffered traces at shutdown (PRD 22/40).
    try:
        from app.observability import flush_telemetry

        flush_telemetry()
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Commerce Gateway",
        version="0.1.0",
        description=(
            "Merchant Agent Layer for Shopify: canonical profiles, agent commerce "
            "capabilities, deterministic policy enforcement, Razorpay test payments, "
            "and full transaction audit trails."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    @app.get("/healthz", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "env": settings.app_env,
            "payment_provider": payment_provider_name(),
        }

    # --- Routers ---------------------------------------------------------------
    from app.agent.routes import router as agent_router
    from app.demo.routes import router as demo_router
    from app.engine.routes import router as team_router
    from app.gateway.routes import router as gateway_router
    from app.onboarding.routes import router as onboarding_router
    from app.transactions.metrics import router as metrics_router
    from app.transactions.payments import router as payments_router
    from app.transactions.routes import router as transactions_router

    if settings.enable_legacy_routes:
        app.include_router(onboarding_router)
        app.include_router(gateway_router)
        app.include_router(transactions_router)
        app.include_router(metrics_router)
        app.include_router(payments_router)
        app.include_router(agent_router)
        app.include_router(demo_router)

    # Strategy-team engine (PRD_3).
    app.include_router(team_router)

    return app


app = create_app()
