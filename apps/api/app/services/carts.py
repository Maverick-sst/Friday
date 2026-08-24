"""Cart service (PRD §11.5).

Client-supplied amounts are never trusted: line prices are recomputed from
the stored quote snapshot server-side.
"""

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.db.base import as_utc
from app.db.models import Cart as CartRow
from app.db.models import Quote as QuoteRow
from app.domain.contracts import Cart as CartContract
from app.domain.contracts import CartItem, CreateCartRequest, QuoteLine


def _new_cart_ref() -> str:
    return f"crt_{secrets.token_hex(8)}"


def create_cart(
    db: Session,
    session_id: str,
    quote_row: QuoteRow,
    req: CreateCartRequest,
) -> tuple[CartContract, CartRow]:
    if as_utc(quote_row.expires_at) <= datetime.now(UTC):
        raise conflict("QUOTE_EXPIRED", f"Quote {quote_row.quote_ref} expired")

    lines: list[QuoteLine] = req.items or [
        QuoteLine(
            product_id=quote_row.product_id,
            variant_id=quote_row.variant_id,
            quantity=quote_row.quantity,
            unit_price_minor=quote_row.subtotal // max(quote_row.quantity, 1),
        )
    ]
    if len(lines) > 10:
        raise conflict("TOO_MANY_LINES", "At most 10 line items per cart in V0")
    if any(line.quantity < 1 for line in lines):
        raise conflict("INVALID_QUANTITY", "Quantities must be >= 1")

    # V0 carts are single-line and must reference the quoted variant.
    unit_price_minor = quote_row.subtotal // max(quote_row.quantity, 1)
    cart_items = []
    total = 0
    for line in lines:
        item_total = unit_price_minor * line.quantity
        cart_items.append(
            CartItem(
                product_id=line.product_id,
                variant_id=line.variant_id,
                quantity=line.quantity,
                unit_price_minor=unit_price_minor,
                total_minor=item_total,
            )
        )
        total += item_total

    row = CartRow(
        cart_ref=_new_cart_ref(),
        merchant_id=quote_row.merchant_id,
        session_id=session_id,
        quote_id=quote_row.id,
        items_json=[item.model_dump() for item in cart_items],
        total_amount=total,
        currency=quote_row.currency,
        status="open",
    )
    db.add(row)
    db.commit()

    contract = CartContract(
        cart_id=row.cart_ref,
        merchant_id=quote_row.merchant_id,
        session_id=session_id,
        quote_id=quote_row.quote_ref,
        items=cart_items,
        total_minor=total,
        currency=row.currency,
        status=row.status,
    )
    return contract, row


def get_cart_by_ref(db: Session, cart_ref: str) -> CartRow:
    row = db.scalar(select(CartRow).where(CartRow.cart_ref == cart_ref))
    if row is None:
        raise not_found("CART_NOT_FOUND", f"No cart {cart_ref}")
    return row


def find_open_cart_for_quote(db: Session, session_id: str, quote_id: str) -> CartRow | None:
    return db.scalar(
        select(CartRow).where(
            CartRow.session_id == session_id,
            CartRow.quote_id == quote_id,
            CartRow.status == "open",
        )
    )


def cart_lines_from_row(row: CartRow):
    from app.services.policy_engine import CartLine

    return [
        CartLine(
            product_id=item["product_id"],
            variant_id=item["variant_id"],
            quantity=int(item["quantity"]),
            unit_price_minor=int(item["unit_price_minor"]),
            total_minor=int(item["total_minor"]),
        )
        for item in (row.items_json or [])
    ]
