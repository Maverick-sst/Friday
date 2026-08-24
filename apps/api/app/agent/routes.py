"""Buyer agent routes: session bootstrap + SSE run stream."""

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import runner
from app.core.errors import not_found
from app.db.models import AgentSession, Merchant
from app.db.session import SessionLocal, get_db
from app.domain.contracts import StartAgentSessionRequest
from app.services.sessions import create_session

router = APIRouter(prefix="/api/v1/agent", tags=["buyer-agent"])

DEFAULT_DEMO_SLUG = "velocity-sports"


@router.post("/sessions")
def start_session(req: StartAgentSessionRequest, db: Session = Depends(get_db)):
    merchant = db.scalar(select(Merchant).where(Merchant.slug == DEFAULT_DEMO_SLUG))
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", "No merchant connected yet - connect or demo-seed first")
    row: AgentSession = create_session(db, req, merchant.id)
    return {
        "session_id": row.session_id,
        "merchant_id": merchant.slug,
        "intent": row.user_intent,
        "constraints": row.constraints_json,
    }


@router.get("/sessions/{session_id}/run")
def run_session(session_id: str):
    """SSE stream of one autonomous buyer-agent run."""
    return StreamingResponse(
        _stream(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream(session_id: str) -> Iterator[str]:
    # Own the DB lifecycle for the duration of this long-lived stream.
    db = SessionLocal()
    try:
        session_row = db.scalar(select(AgentSession).where(AgentSession.session_id == session_id))
        if session_row is None:
            raise not_found("SESSION_NOT_FOUND", f"No agent session {session_id}")
        merchant = db.scalar(select(Merchant).where(Merchant.id == session_row.merchant_id))
        if merchant is None:
            raise not_found("MERCHANT_NOT_FOUND", "Session has no merchant bound")

        for event in runner.run_agent_session(db, session_row, merchant):
            yield f"data: {json.dumps(event.model_dump(), default=str)}\n\n"
        yield "event: done\ndata: {}\n\n"
    finally:
        db.close()
