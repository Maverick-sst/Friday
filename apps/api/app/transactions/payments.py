"""Payment completion callback (client-side Razorpay Checkout result)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.models import AgentSession, Merchant
from app.db.session import get_db
from app.services import checkout as checkout_service
from app.services.sessions import get_session_or_404

router = APIRouter(prefix="/api/v1", tags=["payments"])


class PaymentCompletionRequest(BaseModel):
    order_id: str = Field(min_length=4)
    payment_id: str = Field(min_length=4)
    signature: str = Field(min_length=4)
    session_id: str = Field(min_length=6)


@router.post("/transactions/{txn_ref}/payment/complete")
def complete_payment(txn_ref: str, req: PaymentCompletionRequest, db: Session = Depends(get_db)):
    session_row: AgentSession = get_session_or_404(db, req.session_id)
    merchant = db.scalar(select(Merchant).where(Merchant.id == session_row.merchant_id))
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", "Session has no merchant bound")

    return checkout_service.complete_payment(
        db,
        session_row,
        merchant,
        txn_ref,
        order_id=req.order_id,
        payment_id=req.payment_id,
        signature=req.signature,
    )
