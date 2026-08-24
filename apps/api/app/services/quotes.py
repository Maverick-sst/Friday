"""Quote service: the live-validation boundary (PRD §11.4 / §16).

A quote is an explicit snapshot of authoritative merchant state. Discovery may
trust our DB copy; quotes must not - they always revalidate against the source
platform through the merchant's adapter.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_commerce_adapter
from app.adapters.commerce.base import LiveVariantState
from app.core.errors import conflict, not_found
from app.db.base import as_utc
from app.db.models import AgentSession, Merchant, MerchantIntegration
from app.db.models import Quote as QuoteRow
from app.domain.contracts import Quote as QuoteContract
from app.domain.contracts import QuoteLine, QuoteRequest
from app.domain.enums import Actor, TransactionStatus
from app.services.catalog import get_product_or_404, get_variant
from app.services.sessions import build_authorization
from app.services.transactions import TransactionService

log = logging.getLogger("acg.quotes")

QUOTE_TTL = timedelta(minutes=10)


def _new_quote_ref() -> str:
    return f"qte_{secrets.token_hex(8)}"


def resolve_adapter(db: Session, merchant_id: str):
    integration = db.scalar(
        select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant_id)
    )
    provider = integration.provider if integration else "mock"
    return get_commerce_adapter(provider)


def create_quote(
    db: Session,
    session_row: AgentSession,
    merchant: Merchant,
    req: QuoteRequest,
) -> tuple[QuoteContract, QuoteRow]:
    product, _variants = get_product_or_404(db, merchant.id, req.product_id)
    variant = get_variant(db, product, req.variant_id)
    if variant is None:
        raise not_found("VARIANT_NOT_FOUND", f"No variant {req.variant_id} on this product")

    adapter = resolve_adapter(db, merchant.id)
    external_variant_id = variant.external_id or variant.id
    try:
        live: LiveVariantState = adapter.live_validate_variant(db, merchant.id, external_variant_id)
    except LookupError as exc:
        raise not_found("SOURCE_VARIANT_MISSING", str(exc)) from exc

    now = datetime.now(UTC)
    subtotal = live.price_minor * req.quantity

    quote_row = QuoteRow(
        quote_ref=_new_quote_ref(),
        merchant_id=merchant.id,
        session_id=session_row.session_id,
        product_id=product.id,
        variant_id=variant.id,
        quantity=req.quantity,
        subtotal=subtotal,
        shipping_amount=0,
        tax_amount=0,
        total_amount=subtotal,
        currency=live.currency,
        inventory_snapshot={
            "available_for_sale": live.available_for_sale,
            "available_quantity": live.available_quantity,
            "validated_at": now.isoformat(),
            "provider": adapter.provider,
        },
        live_validated=True,
        source=adapter.provider,
        expires_at=now + QUOTE_TTL,
    )
    db.add(quote_row)
    db.flush()

    # Advance/attach the transaction pipeline deterministically.
    txn_service = TransactionService(db)
    txn = txn_service.get_active_for_session(session_row.session_id)
    if txn is None:
        txn = txn_service.create(session_id=session_row.session_id, merchant_id=merchant.id)

    current = TransactionStatus(txn.status)
    product_ref = product.external_id or product.id
    quote_payload = {
        "quote_ref": quote_row.quote_ref,
        "product": product.title,
        "variant": external_variant_id,
        "quantity": req.quantity,
        "subtotal_minor": subtotal,
        "total_minor": subtotal,
        "currency": live.currency,
        "live_validated": True,
    }
    if current == TransactionStatus.DISCOVERED:
        txn_service.transition(
            txn,
            TransactionStatus.PRODUCT_SELECTED,
            actor=Actor.GATEWAY,
            payload={"product_ref": product_ref},
        )
        current = TransactionStatus.PRODUCT_SELECTED
    if current == TransactionStatus.PRODUCT_SELECTED:
        authz = build_authorization(session_row)
        quote_payload["buyer_max_minor"] = authz.max_amount_minor
        txn_service.transition(
            txn,
            TransactionStatus.QUOTE_CREATED,
            actor=Actor.GATEWAY,
            payload=quote_payload,
        )

    txn.product_id = product.id
    txn.variant_id = variant.id
    txn.quote_id = quote_row.id
    txn.requested_amount = subtotal
    txn.quoted_amount = subtotal
    txn.currency = live.currency
    db.commit()

    contract = QuoteContract(
        quote_id=quote_row.quote_ref,
        merchant_id=merchant.slug,
        session_id=session_row.session_id,
        lines=[
            QuoteLine(
                product_id=product_ref,
                variant_id=external_variant_id,
                quantity=req.quantity,
                unit_price_minor=live.price_minor,
            )
        ],
        subtotal_minor=subtotal,
        shipping_minor=0,
        tax_minor=0,
        total_minor=subtotal,
        currency=live.currency,
        inventory_available=live.available_for_sale,
        expires_at=quote_row.expires_at,
        source=adapter.provider,
        live_validated=True,
    )
    return contract, quote_row


def get_quote_row_by_ref(db: Session, quote_ref: str) -> QuoteRow:
    row = db.scalar(select(QuoteRow).where(QuoteRow.quote_ref == quote_ref))
    if row is None:
        raise not_found("QUOTE_NOT_FOUND", f"No quote {quote_ref}")
    return row


def assert_quote_usable(row: QuoteRow) -> None:
    if as_utc(row.expires_at) <= datetime.now(UTC):
        raise conflict("QUOTE_EXPIRED", f"Quote {row.quote_ref} expired")
