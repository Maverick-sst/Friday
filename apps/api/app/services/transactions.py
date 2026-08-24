"""Transaction lifecycle service.

All financial state changes funnel through `TransactionService.transition`,
which enforces the strict state machine and emits the corresponding audit
event. No other code path may mutate `Transaction.status`.
"""

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.db.models import Transaction, TransactionEvent
from app.domain.enums import Actor, EventType, TransactionStatus
from app.domain.state_machine import InvalidTransition, validate_transition
from app.services.audit import record_event

# Default audit event per transition target.
_DEFAULT_EVENT_FOR_STATE: dict[TransactionStatus, EventType] = {
    TransactionStatus.PRODUCT_SELECTED: EventType.PRODUCT_SELECTED,
    TransactionStatus.QUOTE_CREATED: EventType.QUOTE_CREATED,
    TransactionStatus.POLICY_EVALUATED: EventType.POLICY_EVALUATED,
    TransactionStatus.AUTHORIZED: EventType.AUTHORIZATION_GRANTED,
    TransactionStatus.BLOCKED: EventType.AUTHORIZATION_DENIED,
    TransactionStatus.CART_CREATED: EventType.CART_CREATED,
    TransactionStatus.PAYMENT_PENDING: EventType.PAYMENT_ORDER_CREATED,
    TransactionStatus.PAYMENT_SUCCESS: EventType.PAYMENT_CAPTURED,
    TransactionStatus.PAYMENT_FAILED: EventType.PAYMENT_FAILED,
    TransactionStatus.COMPLETED: EventType.TRANSACTION_COMPLETED,
}


def new_txn_ref() -> str:
    return f"txn_{datetime.now(UTC).strftime('%y%m%d%H%M%S')}_{secrets.token_hex(4)}"


class TransactionService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, session_id: str | None, merchant_id: str) -> Transaction:
        from app.db.base import new_uuid

        txn = Transaction(
            id=new_uuid(),
            txn_ref=new_txn_ref(),
            session_id=session_id,
            merchant_id=merchant_id,
            status=TransactionStatus.DISCOVERED.value,
        )
        self.db.add(txn)
        self.db.flush()
        return txn

    def get_active_for_session(self, session_id: str) -> Transaction | None:
        return self.db.scalar(
            select(Transaction)
            .where(
                Transaction.session_id == session_id,
                Transaction.status.notin_(
                    [
                        TransactionStatus.BLOCKED.value,
                        TransactionStatus.PAYMENT_FAILED.value,
                        TransactionStatus.COMPLETED.value,
                    ]
                ),
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )

    def get_by_ref_or_404(self, txn_ref: str) -> Transaction:
        txn = self.db.scalar(select(Transaction).where(Transaction.txn_ref == txn_ref))
        if txn is None:
            raise not_found("TRANSACTION_NOT_FOUND", f"No transaction {txn_ref}")
        return txn

    def transition(
        self,
        txn: Transaction,
        target: TransactionStatus,
        *,
        actor: Actor = Actor.GATEWAY,
        payload: dict[str, Any] | None = None,
        event_type: EventType | None = None,
        commit: bool = False,
    ) -> Transaction:
        current = TransactionStatus(txn.status)
        try:
            validate_transition(current, target)
        except InvalidTransition as exc:
            raise conflict(
                "INVALID_TRANSACTION_TRANSITION",
                f"Cannot move transaction from {current.value} to {target.value}",
            ) from exc

        txn.status = target.value
        record_event(
            self.db,
            txn.id,
            event_type or _DEFAULT_EVENT_FOR_STATE[target],
            actor,
            {"from": current.value, "to": target.value, **(payload or {})},
        )
        if commit:
            self.db.commit()
        return txn


def get_events(db: Session, transaction_id: str) -> list[TransactionEvent]:
    return list(
        db.scalars(
            select(TransactionEvent)
            .where(TransactionEvent.transaction_id == transaction_id)
            .order_by(TransactionEvent.id.asc())
        )
    )
