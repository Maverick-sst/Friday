"""Checkout orchestrator (PRD §11.6 / §15).

The single gated path to money movement. Sequence is fixed:

    validate quote -> resolve cart -> LIVE revalidation -> Policy Engine
    -> AUTHORIZED ? create payment order : BLOCKED

The amount sent to the provider always comes from the stored, validated quote
row. LLM/agent input can select *what* to buy; it can never set amounts.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_commerce_adapter, get_payment_provider
from app.core.errors import conflict, not_found
from app.core.idempotency import with_idempotency
from app.db.base import as_utc
from app.db.models import (
    AgentSession,
    Merchant,
    MerchantIntegration,
    MerchantPolicy,
    Product,
    ProductVariant,
    Transaction,
)
from app.db.models import (
    Quote as QuoteRow,
)
from app.domain.contracts import CheckoutRequest, CreateCartRequest, PolicyDecision
from app.domain.enums import Actor, EventType, TransactionStatus
from app.services.audit import record_event
from app.services.carts import (
    cart_lines_from_row,
    create_cart,
    find_open_cart_for_quote,
    get_cart_by_ref,
)
from app.services.policy_engine import CartLine, PolicyContext, evaluate
from app.services.quotes import get_quote_row_by_ref
from app.services.sessions import build_authorization
from app.services.transactions import TransactionService

log = logging.getLogger("acg.checkout")


def run_checkout(db: Session, session_row: AgentSession, merchant: Merchant, req: CheckoutRequest) -> dict:
    payload, replayed = with_idempotency(
        db,
        endpoint="checkout",
        scope=session_row.session_id or "anonymous",
        key=req.idempotency_key,
        producer=lambda: _execute_checkout(db, session_row, merchant, req),
    )
    if replayed:
        log.info(
            "checkout idempotent replay",
            extra={"extra_fields": {"session": session_row.session_id, "key": req.idempotency_key}},
        )
    return payload


def _execute_checkout(
    db: Session, session_row: AgentSession, merchant: Merchant, req: CheckoutRequest
) -> dict:
    txn_service = TransactionService(db)
    txn = txn_service.get_active_for_session(session_row.session_id)
    if txn is None or txn.status != TransactionStatus.QUOTE_CREATED.value:
        got = txn.status if txn else "none"
        raise conflict(
            "INVALID_TRANSACTION_STATE",
            f"Checkout requires a transaction in QUOTE_CREATED (got {got})",
        )

    quote_row: QuoteRow = get_quote_row_by_ref(db, req.quote_id)
    if quote_row.merchant_id != merchant.id or quote_row.session_id != session_row.session_id:
        raise not_found("QUOTE_NOT_IN_SESSION", "Quote does not belong to this session/merchant")

    # Expired quotes never touch carts - block deterministically instead.
    if as_utc(quote_row.expires_at) <= datetime.now(UTC):
        return _block(
            txn_service,
            txn,
            reason_codes=["QUOTE_EXPIRED"],
            explanation="The quote expired before checkout.",
        )

    # Resolve cart: explicit ref > open cart for this quote > implicit from quote.
    cart_row = None
    if req.cart_id:
        cart_row = get_cart_by_ref(db, req.cart_id)
        if cart_row.quote_id != quote_row.id:
            raise conflict("CART_QUOTE_MISMATCH", "Cart was built against a different quote")
    else:
        cart_row = find_open_cart_for_quote(db, session_row.session_id, quote_row.id)
    if cart_row is None:
        _, cart_row = create_cart(
            db,
            session_row.session_id,
            quote_row,
            CreateCartRequest(quote_id=req.quote_id),
        )
    cart_lines: list[CartLine] = cart_lines_from_row(cart_row)

    # Live revalidation at checkout time - the stale-data protection boundary.
    adapter = get_commerce_adapter(_provider_of(db, merchant))
    external_variant_id = _external_variant_id(db, quote_row)
    try:
        live_state = adapter.live_validate_variant(db, merchant.id, external_variant_id)
    except LookupError:
        live_state = None

    decision: PolicyDecision = evaluate(
        PolicyContext(
            merchant=merchant,
            policy=_policy_of(db, merchant),
            authorization=build_authorization(session_row),
            product=db.get(Product, quote_row.product_id),
            variant=db.get(ProductVariant, quote_row.variant_id),
            quote=quote_row,
            cart_lines=cart_lines,
            live_state=live_state,
        )
    )

    txn_service.transition(
        txn,
        TransactionStatus.POLICY_EVALUATED,
        actor=Actor.POLICY_ENGINE,
        payload={"allowed": decision.allowed, "reason_codes": decision.reason_codes},
    )

    if not decision.allowed:
        return _block(
            txn_service,
            txn,
            reason_codes=decision.reason_codes,
            explanation=decision.explanation,
            checks=[c.model_dump() for c in decision.checks],
        )

    return _authorize_and_open_order(db, txn_service, txn, merchant, quote_row, cart_row, decision)


def _authorize_and_open_order(
    db: Session,
    txn_service: TransactionService,
    txn: Transaction,
    merchant: Merchant,
    quote_row: QuoteRow,
    cart_row,
    decision: PolicyDecision,
) -> dict:
    authorized_amount = quote_row.total_amount
    txn.authorized_amount = authorized_amount
    txn.final_amount = authorized_amount
    txn.cart_id = cart_row.id
    txn_service.transition(
        txn,
        TransactionStatus.AUTHORIZED,
        actor=Actor.POLICY_ENGINE,
        payload={
            "authorized_minor": authorized_amount,
            "checks_passed": [c.code for c in decision.checks],
        },
    )
    txn_service.transition(
        txn,
        TransactionStatus.CART_CREATED,
        actor=Actor.GATEWAY,
        payload={"cart_ref": cart_row.cart_ref},
    )

    provider = get_payment_provider()
    order = provider.create_order(
        amount_minor=authorized_amount,
        currency=quote_row.currency,
        receipt=txn.txn_ref,
        notes={"txn_ref": txn.txn_ref, "merchant": merchant.slug},
    )
    txn.razorpay_order_id = order.provider_order_id
    txn_service.transition(
        txn,
        TransactionStatus.PAYMENT_PENDING,
        actor=Actor.GATEWAY,
        payload={"provider": provider.name, "order_id": order.provider_order_id},
    )
    db.commit()

    initiation = {
        "provider": provider.name,
        "order_id": order.provider_order_id,
        "amount_minor": authorized_amount,
        "currency": quote_row.currency,
        "txn_ref": txn.txn_ref,
    }
    if provider.name == "razorpay":
        from app.core.config import get_settings

        initiation["key_id"] = get_settings().razorpay_key_id

    return {
        "status": TransactionStatus.PAYMENT_PENDING.value,
        "blocked": False,
        "transaction_id": txn.txn_ref,
        "decision": decision.model_dump(),
        "payment_initiation": initiation,
    }


def _block(
    txn_service: TransactionService,
    txn: Transaction,
    *,
    reason_codes: list[str],
    explanation: str,
    checks: list[dict] | None = None,
) -> dict:
    # The state machine routes every denial through POLICY_EVALUATED.
    if txn.status != TransactionStatus.POLICY_EVALUATED.value:
        txn_service.transition(
            txn,
            TransactionStatus.POLICY_EVALUATED,
            actor=Actor.POLICY_ENGINE,
            payload={"allowed": False, "short_circuit": reason_codes},
        )
    txn_service.transition(
        txn,
        TransactionStatus.BLOCKED,
        actor=Actor.POLICY_ENGINE,
        payload={"reason_codes": reason_codes, "explanation": explanation},
    )
    record_event(
        txn_service.db,
        txn.id,
        EventType.TRANSACTION_BLOCKED,
        Actor.SYSTEM,
        {
            "reason_codes": reason_codes,
        },
    )
    txn_service.db.commit()
    return {
        "status": TransactionStatus.BLOCKED.value,
        "blocked": True,
        "transaction_id": txn.txn_ref,
        "reason_codes": reason_codes,
        "explanation": explanation,
        "checks": checks or [],
    }


def complete_payment(
    db: Session,
    session_row: AgentSession,
    merchant: Merchant,
    txn_ref: str,
    *,
    order_id: str,
    payment_id: str,
    signature: str,
) -> dict:
    """Verify the client-side payment result and finalize the transaction."""
    from app.core.config import get_settings

    txn_service = TransactionService(db)
    txn: Transaction = txn_service.get_by_ref_or_404(txn_ref)

    if txn.status != TransactionStatus.PAYMENT_PENDING.value:
        raise conflict(
            "INVALID_TRANSACTION_STATE",
            f"Payment completion requires PAYMENT_PENDING (got {txn.status})",
        )
    if txn.session_id != session_row.session_id:
        raise not_found("TRANSACTION_NOT_IN_SESSION", "Transaction does not belong to this session")
    stored_order = txn.razorpay_order_id or ""
    if order_id and stored_order and order_id != stored_order:
        raise conflict("ORDER_MISMATCH", "Payment order does not match the transaction")

    provider = get_payment_provider()
    verification = provider.verify_payment_signature(
        razorpay_order_id=stored_order or order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
    )
    txn.razorpay_payment_id = payment_id

    if not verification.verified:
        # Transition itself records the PAYMENT_FAILED audit event.
        txn_service.transition(
            txn,
            TransactionStatus.PAYMENT_FAILED,
            actor=Actor.PAYMENT_PROVIDER,
            payload={"reason": verification.reason or "verification_failed", "payment_id": payment_id},
        )
        db.commit()
        return {
            "status": TransactionStatus.PAYMENT_FAILED.value,
            "transaction_id": txn.txn_ref,
            "reason": verification.reason or "verification_failed",
        }

    # Signature verified -> payment authorized at the provider.
    record_event(
        db,
        txn.id,
        EventType.PAYMENT_AUTHORIZED,
        Actor.PAYMENT_PROVIDER,
        {
            "payment_id": payment_id,
            "order_id": stored_order or order_id,
        },
    )

    capture = None
    settings = get_settings()
    auto_capture = provider.name == "mock" or bool(settings.razorpay_key_secret)
    if auto_capture and txn.final_amount:
        capture = provider.capture_payment(payment_id, int(txn.final_amount), txn.currency)
        if not capture.captured and capture.status not in ("completed", None):
            log.warning("capture result", extra={"extra_fields": {"status": capture.status}})

    txn_service.transition(
        txn,
        TransactionStatus.PAYMENT_SUCCESS,
        actor=Actor.PAYMENT_PROVIDER,
        payload={"payment_id": payment_id, "capture_status": capture.status if capture else "auto"},
    )

    # Push the completed purchase back to the source platform when supported.
    try:
        adapter = get_commerce_adapter(_provider_of(db, merchant))
        source_order = adapter.create_source_order(
            db,
            merchant.id,
            {
                "txn_ref": txn.txn_ref,
                "amount_minor": txn.final_amount,
                "currency": txn.currency,
                "payment_id": payment_id,
            },
        )
        txn.shopify_reference = source_order.reference
    except Exception as exc:
        log.warning("source order push failed", extra={"extra_fields": {"error": str(exc)}})

    txn_service.transition(
        txn,
        TransactionStatus.COMPLETED,
        actor=Actor.SYSTEM,
        payload={"shopify_reference": txn.shopify_reference},
    )
    db.commit()
    return {
        "status": TransactionStatus.COMPLETED.value,
        "transaction_id": txn.txn_ref,
        "payment_id": payment_id,
        "shopify_reference": txn.shopify_reference,
    }


def _provider_of(db: Session, merchant: Merchant) -> str:
    integration = db.scalar(select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant.id))
    return integration.provider if integration else "mock"


def _policy_of(db: Session, merchant: Merchant) -> MerchantPolicy:
    policy = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
    if policy is None:
        raise conflict("POLICY_MISSING", "Merchant has no policy configured")
    return policy


def _external_variant_id(db: Session, quote_row: QuoteRow) -> str:
    variant = db.get(ProductVariant, quote_row.variant_id)
    return (variant.external_id or variant.id) if variant else quote_row.variant_id
