"""Agent Commerce Gateway API entrypoint."""

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

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
        finally:
            request_id_ctx.reset(token)
        response.headers["x-request-id"] = rid
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Commerce Gateway",
        version="0.1.0",
        description=(
            "Merchant Agent Layer for Shopify: canonical profiles, agent commerce "
            "capabilities, deterministic policy enforcement, Razorpay test payments, "
            "and full transaction audit trails."
        ),
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
        return {"status": "ok", "env": settings.app_env}

    # Routers are registered here as phases land:
    # onboarding, gateway, transactions, agent, demo.
    return app


app = create_app()
