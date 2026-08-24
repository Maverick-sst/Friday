"""Agent Commerce Gateway routes (PRD §27 agent surface).

These are the only endpoints buyer agents interact with. Every route validates
input with typed schemas; checkout is idempotent and policy-gated.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import GatewayError, not_found
from app.db.models import (
    Merchant,
    MerchantCapability,
    MerchantIntegration,
    MerchantPolicy,
)
from app.db.session import get_db
from app.domain.contracts import (
    CheckoutRequest,
    CreateCartRequest,
    QuoteRequest,
    SearchProductsRequest,
)
from app.domain.enums import Actor, EventType, TransactionStatus
from app.services import carts as cart_service
from app.services import catalog as catalog_service
from app.services import checkout as checkout_service
from app.services import quotes as quote_service
from app.services.audit import record_event
from app.services.profile_builder import build_profile
from app.services.sessions import get_session_or_404
from app.services.transactions import TransactionService

router = APIRouter(prefix="/api/v1/merchants", tags=["agent-gateway"])


def _merchant_or_404(db: Session, slug: str) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.slug == slug))
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", f"No merchant {slug}")
    return merchant


def _session_from_header(request: Request, db: Session):
    session_id = request.headers.get("x-session-id")
    if not session_id:
        raise GatewayError(
            "SESSION_REQUIRED",
            "Provide the X-Session-Id header issued when the buyer-agent session started",
            status_code=401,
        )
    return get_session_or_404(db, session_id)


@router.get("/{merchant_id}/discover")
def discover(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    integration = db.scalar(select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant.id))
    profile = build_profile(db, merchant, integration=integration)
    return {
        "merchant_id": profile.merchant.id,
        "name": profile.merchant.name,
        "category": profile.merchant.category,
        "capabilities": [c.value for c in profile.commerce.capabilities],
        "policies": {
            "max_auto_purchase_minor": profile.policies.max_auto_purchase_minor,
            "currency": profile.policies.currency,
            "return_window_days": profile.policies.return_window_days,
        },
        "source": profile.source.model_dump(),
        "profile": profile.model_dump(),
    }


@router.post("/{merchant_id}/search")
def search_products(
    merchant_id: str, req: SearchProductsRequest, db: Session = Depends(get_db)
):
    merchant = _merchant_or_404(db, merchant_id)
    results = catalog_service.search_products(db, merchant.id, req)

    # Best-effort audit: attach PRODUCT_SEARCHED to any active txn in session.
    session_id = None  # search is unauthenticated discovery; no session required
    del session_id

    return {"query": req.query, "count": len(results), "products": [p.model_dump() for p in results]}


@router.get("/{merchant_id}/products/{product_ref}")
def get_product(merchant_id: str, product_ref: str, request: Request, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    product, variants = catalog_service.get_product_or_404(db, merchant.id, product_ref)

    session_id = request.headers.get("x-session-id")
    if session_id:
        session_row = get_session_or_404(db, session_id)
        txn_service = TransactionService(db)
        txn = txn_service.get_active_for_session(session_row.session_id)
        if txn is not None and txn.status == TransactionStatus.DISCOVERED.value:
            txn_service.transition(
                txn,
                TransactionStatus.PRODUCT_SELECTED,
                actor=Actor.BUYER_AGENT,
                payload={"product_ref": product.external_id or product.id},
            )
            db.commit()

    from app.services.catalog import _to_contract

    contract = _to_contract(product, variants)
    return contract.model_dump()


@router.post("/{merchant_id}/quote")
def create_quote(merchant_id: str, req: QuoteRequest, request: Request, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    session_row = _session_from_header(request, db)
    quote_contract, _row = quote_service.create_quote(db, session_row, merchant, req)
    return quote_contract.model_dump()


@router.post("/{merchant_id}/cart")
def create_cart(merchant_id: str, req: CreateCartRequest, request: Request, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    session_row = _session_from_header(request, db)
    quote_row = quote_service.get_quote_row_by_ref(db, req.quote_id)
    if quote_row.merchant_id != merchant.id or quote_row.session_id != session_row.session_id:
        raise not_found("QUOTE_NOT_IN_SESSION", "Quote does not belong to this session/merchant")

    contract, row = cart_service.create_cart(db, session_row.session_id, quote_row, req)

    # CART_CREATED is a real state only after authorization; pre-checkout carts
    # still produce an audit event for the timeline.
    txn_service = TransactionService(db)
    txn = txn_service.get_active_for_session(session_row.session_id)
    if txn is not None:
        record_event(
            db,
            txn.id,
            EventType.CART_CREATED,
            Actor.BUYER_AGENT,
            {"cart_ref": row.cart_ref, "total_minor": row.total_amount, "stage": "pre-checkout"},
        )
        db.commit()

    return contract.model_dump()


@router.post("/{merchant_id}/checkout")
def checkout(merchant_id: str, req: CheckoutRequest, request: Request, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    session_row = _session_from_header(request, db)
    result = checkout_service.run_checkout(db, session_row, merchant, req)
    status_code = 200 if not result.get("blocked") else 402
    import json

    from fastapi import Response

    return Response(
        content=json.dumps(result),
        status_code=status_code,
        media_type="application/json",
    )


@router.get("/{merchant_id}/capabilities")
def capabilities(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    rows = list(db.scalars(select(MerchantCapability).where(MerchantCapability.merchant_id == merchant.id)))
    return {
        "merchant_id": merchant.slug,
        "capabilities": [
            {"name": r.capability_name, "enabled": r.enabled, "version": r.version}
            for r in rows
        ],
    }


@router.get("/{merchant_id}/policies")
def get_policies(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    policy = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
    if policy is None:
        raise not_found("POLICY_MISSING", "Merchant has no policies configured")
    return {
        "max_auto_purchase_minor": policy.max_auto_purchase,
        "approval_threshold_minor": policy.approval_threshold,
        "allowed_categories": policy.allowed_categories_json,
        "currency": policy.currency,
        "return_window_days": policy.return_window_days,
        "allow_cancellation": policy.allow_cancellation,
        "version": policy.version,
    }
