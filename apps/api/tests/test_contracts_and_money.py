from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.contracts import (
    BuyerAuthorization,
    CheckoutRequest,
    MerchantAgentProfile,
    MerchantCommerce,
    MerchantIdentity,
    MerchantPolicyView,
    MerchantSourceInfo,
    QuoteLine,
    SearchFilters,
    StartAgentSessionRequest,
)
from app.domain.enums import Capability
from app.domain.money import format_minor, major_to_minor


def _profile(**policy_overrides):
    policy_kwargs = {
        "max_auto_purchase_minor": 500000,
        "approval_threshold_minor": 500000,
        "allowed_categories": ["running_shoes", "sportswear"],
    }
    policy_kwargs.update(policy_overrides)
    policy = MerchantPolicyView(**policy_kwargs)
    return MerchantAgentProfile(
        merchant=MerchantIdentity(id="velocity-sports-001", name="Velocity Sports"),
        commerce=MerchantCommerce(currency="INR", capabilities=[Capability.CHECKOUT]),
        policies=policy,
        source=MerchantSourceInfo(provider="shopify", store_url="https://v.myshopify.com"),
    )


class TestMerchantProfile:
    def test_minimal_valid_profile(self):
        p = _profile()
        assert p.merchant.name == "Velocity Sports"
        assert Capability.CHECKOUT in p.commerce.capabilities

    def test_negative_policy_amounts_rejected(self):
        with pytest.raises(ValidationError):
            _profile(max_auto_purchase_minor=-1)

    def test_profile_roundtrips_json(self):
        p = _profile()
        clone = MerchantAgentProfile.model_validate_json(p.model_dump_json())
        assert clone == p


class TestBuyerAuthorization:
    def test_requires_positive_limit(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            BuyerAuthorization(max_amount_minor=0, issued_at=now)

    def test_expiry_field(self):
        now = datetime.now(UTC)
        auth = BuyerAuthorization(max_amount_minor=500000, issued_at=now, expires_at=now + timedelta(minutes=30))
        assert auth.expires_at > auth.issued_at


class TestGatewayPayloads:
    def test_search_filters_forbid_unknown_fields(self):
        with pytest.raises(ValidationError):
            SearchFilters(nonsense=True)

    def test_checkout_requires_idempotency_key(self):
        with pytest.raises(ValidationError):
            CheckoutRequest(quote_id="q1", idempotency_key="short")

    def test_cart_item_quantity_bounds(self):
        with pytest.raises(ValidationError):
            QuoteLine(product_id="p", variant_id="v", quantity=0)

    def test_agent_session_budget_required(self):
        req = StartAgentSessionRequest(intent="buy shoes under 5000", max_budget_minor=500000)
        assert req.max_budget_minor == 500000
        with pytest.raises(ValidationError):
            StartAgentSessionRequest(intent="x", max_budget_minor=0)


class TestMoney:
    def test_major_to_minor(self):
        assert major_to_minor("4799") == 479900
        assert major_to_minor(5799.5) == 579950

    def test_format_inr(self):
        assert format_minor(479900) == "\u20b94,799.00"
