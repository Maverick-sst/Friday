"""Buyer agent session routes (Phase 6 fills the SSE run stream)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentSession, Merchant
from app.db.session import get_db
from app.domain.contracts import StartAgentSessionRequest
from app.services.sessions import create_session

router = APIRouter(prefix="/api/v1/agent", tags=["buyer-agent"])

DEFAULT_DEMO_SLUG = "velocity-sports"


@router.post("/sessions")
def start_session(req: StartAgentSessionRequest, db: Session = Depends(get_db)):
    merchant = db.scalar(select(Merchant).where(Merchant.slug == DEFAULT_DEMO_SLUG))
    if merchant is None:
        from app.core.errors import not_found

        raise not_found("MERCHANT_NOT_FOUND", "No merchant connected yet - connect or demo-seed first")
    row: AgentSession = create_session(db, req, merchant.id)
    return {
        "session_id": row.session_id,
        "merchant_id": merchant.slug,
        "intent": row.user_intent,
        "constraints": row.constraints_json,
    }
