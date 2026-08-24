"""Buyer agent session management.

Sessions carry the user's intent and the structured spending constraints that
become the deterministic BuyerAuthorization. Constraints are captured as
typed fields - never parsed out of free text by an LLM (PRD §7.1 vs §7.2).
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.models import AgentSession
from app.domain.contracts import BuyerAuthorization, StartAgentSessionRequest

AUTHORIZATION_WINDOW_MINUTES = 30


def _new_session_id() -> str:
    return f"sess_{secrets.token_hex(8)}"


def create_session(db: Session, req: StartAgentSessionRequest, merchant_id: str | None) -> AgentSession:
    session = AgentSession(
        session_id=_new_session_id(),
        buyer_id="demo-user",
        merchant_id=merchant_id,
        user_intent=req.intent,
        constraints_json={
            "max_budget_minor": req.max_budget_minor,
            "currency": req.currency,
            "preferred_size": req.preferred_size,
            "preferred_category": req.preferred_category,
            "demo_scenario": req.demo_scenario,
        },
    )
    db.add(session)
    db.commit()
    return session


def get_session_or_404(db: Session, session_id: str) -> AgentSession:
    row = db.scalar(select(AgentSession).where(AgentSession.session_id == session_id))
    if row is None:
        raise not_found("SESSION_NOT_FOUND", f"No agent session {session_id}")
    return row


def build_authorization(session: AgentSession) -> BuyerAuthorization:
    c = session.constraints_json or {}
    now = datetime.now(UTC)
    return BuyerAuthorization(
        buyer_id=session.buyer_id or "demo-user",
        max_amount_minor=int(c.get("max_budget_minor", 0)),
        currency=c.get("currency", "INR"),
        allowed_categories=[c["preferred_category"]] if c.get("preferred_category") else None,
        intent=session.user_intent or "",
        issued_at=now,
        expires_at=now + timedelta(minutes=AUTHORIZATION_WINDOW_MINUTES),
    )
