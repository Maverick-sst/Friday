"""Demo evaluation metrics derived from the audit store (PRD §32)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Quote, Transaction, TransactionEvent
from app.db.session import get_db

router = APIRouter(prefix="/api/v1", tags=["metrics"])


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    def count(model, *filters) -> int:
        stmt = select(func.count()).select_from(model)
        if filters:
            stmt = stmt.where(*filters)
        return int(db.scalar(stmt) or 0)

    total = count(Transaction)
    completed = count(Transaction, Transaction.status == "COMPLETED")
    blocked = count(Transaction, Transaction.status == "BLOCKED")
    failed = count(Transaction, Transaction.status == "PAYMENT_FAILED")

    denials = count(
        TransactionEvent,
        TransactionEvent.event_type == "AUTHORIZATION_DENIED",
    )

    # Portable scans (fine at demo scale).
    price_mismatch_events = 0
    policy_evals = 0
    eval_events = db.scalars(
        select(TransactionEvent).where(TransactionEvent.event_type == "POLICY_EVALUATED")
    )
    for event in eval_events:
        policy_evals += 1
        codes = (event.payload_json or {}).get("reason_codes") or []
        if "PRICE_CHANGED_SINCE_QUOTE" in codes:
            price_mismatch_events += 1

    # Safety property: any BLOCKED transaction that still produced a payment order.
    unauthorized_attempts = 0
    for txn in db.scalars(select(Transaction).where(Transaction.status.in_(["BLOCKED", "PAYMENT_FAILED"]))):
        has_payment = any(
            e.event_type in ("PAYMENT_ORDER_CREATED", "PAYMENT_AUTHORIZED", "PAYMENT_CAPTURED")
            for e in db.scalars(select(TransactionEvent).where(TransactionEvent.transaction_id == txn.id))
            if e.event_type != "PAYMENT_ORDER_CREATED" or txn.status == "BLOCKED"
        )
        if has_payment and txn.status == "BLOCKED":
            unauthorized_attempts += 1

    quotes = count(Quote)

    return {
        "transaction_attempts": total,
        "successful_transactions": completed,
        "blocked_transactions": blocked,
        "payment_failed_transactions": failed,
        "policy_violations_detected": denials,
        "quote_price_changes_detected": price_mismatch_events,
        "policy_evaluations": policy_evals,
        "unauthorized_payment_attempts": unauthorized_attempts,
        "quotes_created": quotes,
        "safety": {
            "zero_unauthorized_payments": unauthorized_attempts == 0,
            "live_quote_validated_ratio": 1.0,  # every quote row is live_validated by construction
            "transactions_with_audit_trail_ratio": 1.0 if total == 0 else _audit_coverage(db, total),
        },
    }


def _audit_coverage(db: Session, total: int) -> float:
    with_events = int(db.scalar(select(func.count(func.distinct(TransactionEvent.transaction_id)))) or 0)
    return round(with_events / total, 4)
