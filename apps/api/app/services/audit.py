"""Audit trail writer (PRD §20). Every meaningful decision becomes an event."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import TransactionEvent
from app.domain.enums import Actor, EventType

_logger = logging.getLogger("acg.audit")
_INFO = logging.INFO


def record_event(
    db: Session,
    transaction_id: str,
    event_type: EventType,
    actor: Actor,
    payload: dict[str, Any] | None = None,
    commit: bool = False,
) -> TransactionEvent:
    if db is None:
        raise ValueError("audit.record_event requires a db session")
    event = TransactionEvent(
        transaction_id=transaction_id,
        event_type=event_type.value,
        actor=actor.value,
        payload_json=payload or {},
    )
    db.add(event)
    db.flush()
    log_event(_logger, _INFO, "event", txn=transaction_id, type=event_type.value, actor=actor.value)
    return event


def log_event(logger: logging.Logger, level: int, message: str, **fields: object) -> None:
    logger.log(level, message, extra={"extra_fields": fields})
