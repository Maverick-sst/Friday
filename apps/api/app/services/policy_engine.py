"""Deterministic policy engine (PRD §14).

Pure, synchronous, LLM-free. Evaluates every rule and reports all failures
with stable reason codes. This module is the financial authority of the
platform: nothing reaches a payment provider without an `allowed=True`
decision produced here.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.adapters.commerce.base import LiveVariantState
from app.db.base import as_utc
from app.db.models import Merchant, MerchantPolicy, Product, ProductVariant, Quote
from app.domain import reason_codes as rc
from app.domain.contracts import BuyerAuthorization, PolicyCheck, PolicyDecision
from app.domain.money import format_minor


@dataclass(slots=True)
class CartLine:
    product_id: str
    variant_id: str
    quantity: int
    unit_price_minor: int
    total_minor: int


@dataclass(slots=True)
class PolicyContext:
    merchant: Merchant
    policy: MerchantPolicy
    authorization: BuyerAuthorization
    product: Product | None
    variant: ProductVariant | None
    quote: Quote
    cart_lines: list[CartLine]
    live_state: LiveVariantState | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


def evaluate(ctx: PolicyContext) -> PolicyDecision:
    checks: list[PolicyCheck] = []
    total = ctx.quote.total_amount

    def add(code: str, passed: bool, detail: str) -> None:
        checks.append(PolicyCheck(code=code, passed=passed, detail=detail))

    # 1. Merchant is active.
    merchant_active = ctx.merchant.status == "active"
    add(rc.MERCHANT_INACTIVE, merchant_active, f"merchant status={ctx.merchant.status}")

    # 2. Product exists and is active.
    if ctx.product is None:
        add(rc.PRODUCT_NOT_FOUND, False, "product row missing")
    else:
        add(
            rc.PRODUCT_ARCHIVED,
            ctx.product.status == "active",
            f"product status={ctx.product.status}",
        )

    # 3. Variant exists.
    variant_ok = ctx.variant is not None
    add(rc.VARIANT_NOT_FOUND, variant_ok, "variant row missing" if not variant_ok else "variant resolved")

    # 4. Live availability.
    live = ctx.live_state
    if live is None:
        add(rc.INVENTORY_UNAVAILABLE, False, "no live state available")
    elif not live.available_for_sale:
        qty = live.available_quantity if live.available_quantity is not None else 0
        add(rc.VARIANT_UNAVAILABLE, False, f"live availability false (qty={qty})")
    else:
        add(rc.VARIANT_UNAVAILABLE, True, f"live qty={live.available_quantity}")

    # 5. Quote freshness.
    expired = as_utc(ctx.quote.expires_at) <= ctx.now
    add(
        rc.QUOTE_EXPIRED,
        not expired,
        f"expired_at={as_utc(ctx.quote.expires_at).isoformat()}",
    )

    # 6. Currency alignment across quote/authorization/policy.
    currency_ok = (
        ctx.quote.currency == ctx.authorization.currency == ctx.policy.currency
    )
    add(
        rc.CURRENCY_MISMATCH,
        currency_ok,
        f"quote={ctx.quote.currency} auth={ctx.authorization.currency} policy={ctx.policy.currency}",
    )

    # 7. Buyer authorized ceiling.
    buyer_ok = total <= ctx.authorization.max_amount_minor
    add(
        rc.FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION,
        buyer_ok,
        f"total {format_minor(total)} vs buyer limit "
        f"{format_minor(ctx.authorization.max_amount_minor)}",
    )

    # 8. Merchant automatic-purchase ceiling.
    merchant_limit_ok = total <= ctx.policy.max_auto_purchase
    add(
        rc.FINAL_AMOUNT_EXCEEDS_MERCHANT_LIMIT,
        merchant_limit_ok,
        f"total {format_minor(total)} vs merchant cap "
        f"{format_minor(ctx.policy.max_auto_purchase)}",
    )

    # 9. Category allowed by merchant policy AND buyer constraints.
    category = ctx.product.category if ctx.product else None
    policy_cat_ok = bool(category) and category in (ctx.policy.allowed_categories_json or [])
    buyer_cat_ok = ctx.authorization.allowed_categories is None or (
        bool(category) and category in ctx.authorization.allowed_categories
    )
    add(
        rc.CATEGORY_NOT_ALLOWED,
        policy_cat_ok and buyer_cat_ok,
        f"category={category} policy_allowed={ctx.policy.allowed_categories_json} "
        f"buyer_allowed={ctx.authorization.allowed_categories}",
    )

    # 10. Buyer constraint set still satisfied by the merchant (V0: expiry).
    auth_valid = ctx.authorization.expires_at is None or ctx.authorization.expires_at > ctx.now
    add(
        rc.BUYER_AUTHORIZATION_EXPIRED,
        auth_valid,
        "authorization valid" if auth_valid else "authorization expired",
    )

    # 11. Cart total matches quoted total.
    cart_total = sum(line.total_minor for line in ctx.cart_lines)
    cart_ok = cart_total == total and len(ctx.cart_lines) > 0
    add(
        rc.CART_TOTAL_MISMATCH,
        cart_ok,
        f"cart {format_minor(cart_total)} vs quote {format_minor(total)}",
    )

    # 12. No material change since quote (live price revalidation).
    if live is None:
        add(rc.PRICE_CHANGED_SINCE_QUOTE, False, "cannot confirm price without live state")
    else:
        expected_unit = ctx.quote.subtotal // max(ctx.quote.quantity, 1)
        price_stable = live.price_minor == expected_unit
        add(
            rc.PRICE_CHANGED_SINCE_QUOTE,
            price_stable,
            f"quoted unit {format_minor(expected_unit)} vs live {format_minor(live.price_minor)}",
        )
        if not price_stable:
            # Re-check ceilings against the effective (live) amount so a raised
            # price cannot sneak past the authorization limits.
            live_total = live.price_minor * max(ctx.quote.quantity, 1)
            add(
                rc.FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION,
                live_total <= ctx.authorization.max_amount_minor,
                f"live total {format_minor(live_total)} vs buyer limit "
                f"{format_minor(ctx.authorization.max_amount_minor)}",
            )
            add(
                rc.FINAL_AMOUNT_EXCEEDS_MERCHANT_LIMIT,
                live_total <= ctx.policy.max_auto_purchase,
                f"live total {format_minor(live_total)} vs merchant cap "
                f"{format_minor(ctx.policy.max_auto_purchase)}",
            )

    reason_codes = [c.code for c in checks if not c.passed]
    allowed = len(reason_codes) == 0

    explanation = (
        f"Authorized {format_minor(total)} ({ctx.quote.currency}) - all policy checks passed."
        if allowed
        else _explain(reason_codes, total, ctx)
    )
    return PolicyDecision(allowed=allowed, reason_codes=reason_codes, explanation=explanation, checks=checks)


def _explain(reason_codes: list[str], total: int, ctx: PolicyContext) -> str:
    parts: list[str] = []
    for code in reason_codes:
        if code == rc.FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION:
            parts.append(
                f"Final quote {format_minor(total)} exceeds the authorized "
                f"{format_minor(ctx.authorization.max_amount_minor)} limit."
            )
        elif code == rc.FINAL_AMOUNT_EXCEEDS_MERCHANT_LIMIT:
            parts.append(
                f"Final quote {format_minor(total)} exceeds the merchant "
                f"automatic-purchase cap {format_minor(ctx.policy.max_auto_purchase)}."
            )
        elif code == rc.PRICE_CHANGED_SINCE_QUOTE:
            live_price = ctx.live_state.price_minor if ctx.live_state else None
            if live_price is not None:
                unit_quoted = ctx.quote.subtotal // max(ctx.quote.quantity, 1)
                parts.append(
                    f"The price changed from {format_minor(unit_quoted)} to "
                    f"{format_minor(live_price)} after the quote was issued."
                )
            else:
                parts.append("Live price could not be confirmed.")
        elif code == rc.VARIANT_UNAVAILABLE:
            parts.append("The selected size is no longer available at the merchant.")
        elif code == rc.INVENTORY_UNAVAILABLE:
            parts.append("Inventory could not be confirmed at the merchant.")
        elif code == rc.QUOTE_EXPIRED:
            parts.append("The quote expired before checkout.")
        elif code == rc.CATEGORY_NOT_ALLOWED:
            parts.append("This product category is not permitted for autonomous purchase.")
        elif code == rc.CURRENCY_MISMATCH:
            parts.append("Currency mismatch between quote, authorization, and merchant policy.")
        elif code == rc.CART_TOTAL_MISMATCH:
            parts.append("Cart total does not match the quoted amount.")
        elif code == rc.BUYER_AUTHORIZATION_EXPIRED:
            parts.append("The buyer authorization window expired.")
        elif code == rc.MERCHANT_INACTIVE:
            parts.append("The merchant is not currently accepting agent transactions.")
        elif code in (rc.PRODUCT_NOT_FOUND, rc.PRODUCT_ARCHIVED):
            parts.append("The selected product is no longer available.")
        elif code == rc.VARIANT_NOT_FOUND:
            parts.append("The selected variant no longer exists.")
        else:
            parts.append(f"Policy check failed: {code}.")
    return " ".join(parts)
