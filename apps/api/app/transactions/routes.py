"""Transaction trace APIs (PRD §27 / §20): the audit timeline surface."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.transactions import TransactionService, get_events

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


def _txn_payload(txn) -> dict:
    return {
        "transaction_id": txn.txn_ref,
        "session_id": txn.session_id,
        "merchant_id": txn.merchant_id,
        "status": txn.status,
        "requested_amount_minor": txn.requested_amount,
        "quoted_amount_minor": txn.quoted_amount,
        "authorized_amount_minor": txn.authorized_amount,
        "final_amount_minor": txn.final_amount,
        "currency": txn.currency,
        "shopify_reference": txn.shopify_reference,
        "razorpay_order_id": txn.razorpay_order_id,
        "razorpay_payment_id": txn.razorpay_payment_id,
        "created_at": txn.created_at.isoformat() if txn.created_at else None,
        "updated_at": txn.updated_at.isoformat() if txn.updated_at else None,
    }


@router.get("/{txn_ref}")
def get_transaction(txn_ref: str, db: Session = Depends(get_db)):
    txn = TransactionService(db).get_by_ref_or_404(txn_ref)
    return _txn_payload(txn)


@router.get("/{txn_ref}/events")
def get_transaction_events(txn_ref: str, db: Session = Depends(get_db)):
    txn = TransactionService(db).get_by_ref_or_404(txn_ref)
    return {
        "transaction_id": txn.txn_ref,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "actor": e.actor,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "payload": e.payload_json or {},
            }
            for e in get_events(db, txn.id)
        ],
    }
