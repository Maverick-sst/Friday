"""Deterministic policy engine unit tests (PRD §14 rules)."""

from datetime import UTC, datetime, timedelta

from app.adapters.commerce.base import LiveVariantState
from app.db.models import Merchant, MerchantPolicy, Product, ProductVariant, Quote
from app.domain import reason_codes as rc
from app.domain.contracts import BuyerAuthorization
from app.domain.money import major_to_minor
from app.services.policy_engine import CartLine, PolicyContext, evaluate


def _ctx(
    *,
    total_minor=479900,
    buyer_max_minor=500000,
    merchant_max_minor=500000,
    live_price_minor=None,
    live_available=True,
    quote_expired=False,
    category="running_shoes",
    allowed_categories=("running_shoes", "sportswear"),
    quantity=1,
):
    now = datetime.now(UTC)
    merchant = Merchant(name="V", slug="v", status="active")
    policy = MerchantPolicy(
        merchant_id="m",
        max_auto_purchase=merchant_max_minor,
        approval_threshold=merchant_max_minor,
        allowed_categories_json=list(allowed_categories),
        currency="INR",
    )
    auth = BuyerAuthorization(
        max_amount_minor=buyer_max_minor,
        currency="INR",
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    product = Product(merchant_id="m", title="Shoe", category=category, status="active", external_id="p1")
    variant = ProductVariant(
        product_id="x",
        external_id="v1",
        price=total_minor // quantity,
        currency="INR",
        available_for_sale=True,
    )
    quote = Quote(
        quote_ref="q1",
        merchant_id="m",
        product_id="pid",
        variant_id="vid",
        quantity=quantity,
        subtotal=total_minor,
        shipping_amount=0,
        tax_amount=0,
        total_amount=total_minor,
        currency="INR",
        expires_at=now - timedelta(minutes=5) if quote_expired else now + timedelta(minutes=10),
    )
    lines = [
        CartLine(
            product_id="pid",
            variant_id="v1",
            quantity=quantity,
            unit_price_minor=total_minor // quantity,
            total_minor=total_minor,
        )
    ]
    live = None
    if live_price_minor is not None or live_available is not None:
        live = LiveVariantState(
            external_variant_id="v1",
            external_product_id="p1",
            price_minor=live_price_minor if live_price_minor is not None else total_minor // quantity,
            currency="INR",
            available_for_sale=live_available if live_available is not None else True,
            available_quantity=7,
        )
    return PolicyContext(
        merchant=merchant,
        policy=policy,
        authorization=auth,
        product=product,
        variant=variant,
        quote=quote,
        cart_lines=lines,
        live_state=live,
        now=now,
    )


class TestHappyPath:
    def test_authorized_when_everything_aligns(self):
        decision = evaluate(_ctx())
        assert decision.allowed, decision.explanation
        assert decision.reason_codes == []
        assert len(decision.checks) == 12  # PRD §14: all twelve rules evaluated


class TestBlocks:
    def test_price_change_after_quote_is_blocked(self):
        """The mandatory failure demo rule."""
        decision = evaluate(_ctx(live_price_minor=major_to_minor("5799")))
        assert not decision.allowed
        assert rc.PRICE_CHANGED_SINCE_QUOTE in decision.reason_codes

    def test_over_buyer_budget_blocked(self):
        decision = evaluate(_ctx(total_minor=major_to_minor("5799")))
        assert not decision.allowed
        assert rc.FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION in decision.reason_codes

    def test_over_merchant_limit_blocked(self):
        decision = evaluate(
            _ctx(total_minor=major_to_minor("5500"), merchant_max_minor=major_to_minor("5000"))
        )
        assert not decision.allowed
        assert rc.FINAL_AMOUNT_EXCEEDS_MERCHANT_LIMIT in decision.reason_codes

    def test_inventory_gone_blocked(self):
        decision = evaluate(_ctx(live_available=False))
        assert not decision.allowed
        assert rc.VARIANT_UNAVAILABLE in decision.reason_codes

    def test_quote_expiry_blocked(self):
        decision = evaluate(_ctx(quote_expired=True))
        assert not decision.allowed
        assert rc.QUOTE_EXPIRED in decision.reason_codes

    def test_category_not_allowed_blocked(self):
        decision = evaluate(_ctx(category="electronics"))
        assert not decision.allowed
        assert rc.CATEGORY_NOT_ALLOWED in decision.reason_codes

    def test_cart_total_mismatch_blocked(self):
        ctx = _ctx()
        ctx.cart_lines[0].total_minor = 99900
        decision = evaluate(ctx)
        assert not decision.allowed
        assert rc.CART_TOTAL_MISMATCH in decision.reason_codes

    def test_inactive_merchant_blocked(self):
        ctx = _ctx()
        ctx.merchant.status = "disabled"
        decision = evaluate(ctx)
        assert not decision.allowed
        assert rc.MERCHANT_INACTIVE in decision.reason_codes

    def test_quantity_multiplied_price_drift_detected(self):
        big = major_to_minor("20000")
        decision = evaluate(
            _ctx(
                quantity=2,
                total_minor=major_to_minor("9598"),
                live_price_minor=major_to_minor("4799"),
                buyer_max_minor=big,
                merchant_max_minor=big,
            )
        )
        assert decision.allowed, decision.explanation  # 2 x 4799 = 9598 consistent

        decision2 = evaluate(
            _ctx(
                quantity=2,
                total_minor=major_to_minor("9598"),
                live_price_minor=major_to_minor("4899"),
                buyer_max_minor=big,
                merchant_max_minor=big,
            )
        )
        assert not decision2.allowed
        assert rc.PRICE_CHANGED_SINCE_QUOTE in decision2.reason_codes

    def test_all_reasons_collected_not_first_failure(self):
        decision = evaluate(_ctx(live_available=False, quote_expired=True))
        assert rc.VARIANT_UNAVAILABLE in decision.reason_codes
        assert rc.QUOTE_EXPIRED in decision.reason_codes
        assert len(decision.reason_codes) == 2
