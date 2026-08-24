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
    yield


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
    from app.gateway.routes import router as gateway_router
    from app.onboarding.routes import router as onboarding_router
    from app.transactions.payments import router as payments_router
    from app.transactions.routes import router as transactions_router

    app.include_router(onboarding_router)
    app.include_router(gateway_router)
    app.include_router(transactions_router)
    app.include_router(payments_router)
    app.include_router(agent_router)
    app.include_router(demo_router)

    return app


app = create_app()
