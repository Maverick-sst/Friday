"""End-to-end checkout flow tests against the mock adapter (service layer).

Covers the full buyer journey: session -> search -> quote -> cart ->
checkout -> payment completion, plus the mandatory blocked paths.
"""

import pytest

from app.db.demo_overrides import DemoOverride
from app.db.seeds import seed_mock_merchant
from app.domain.contracts import (
    CheckoutRequest,
    CreateCartRequest,
    QuoteRequest,
    SearchProductsRequest,
    StartAgentSessionRequest,
)
from app.domain.enums import TransactionStatus
from app.domain.money import major_to_minor
from app.services import carts as cart_service
from app.services import catalog as catalog_service
from app.services import checkout as checkout_service
from app.services import quotes as quote_service
from app.services.sessions import create_session
from app.services.transactions import TransactionService, get_events

IDEMPOTENCY_SEQ = iter(range(1000, 9000))


@pytest.fixture()
def seeded(db_session):
    merchant = seed_mock_merchant(db_session)
    return db_session, merchant


def _session(db, merchant, budget_major="5000"):
    return create_session(
        db,
        StartAgentSessionRequest(
            intent="Find Nike Downshifter 14, size 9, under INR 5,000",
            max_budget_minor=major_to_minor(budget_major),
        ),
        merchant_id=merchant.id,
    )


def _quote_size9(db, session_row, merchant):
    product, variants = catalog_service.get_product_or_404(db, merchant.id, "mock-prod-downshifter")
    variant = next(v for v in variants if v.external_id == "mock-var-ds-9-black")
    contract, row = quote_service.create_quote(
        db,
        session_row,
        merchant,
        QuoteRequest(product_id=product.id, variant_id=variant.id, quantity=1),
    )
    return contract, row, product, variant


def _idem_key() -> str:
    return f"itest-{next(IDEMPOTENCY_SEQ):05d}"


class TestHappyPath:
    def test_full_purchase_completes(self, seeded):
        db, merchant = seeded

        results = catalog_service.search_products(
            db, merchant.id, SearchProductsRequest(query="downshifter", limit=5)
        )
        assert len(results) == 1
        assert results[0].title == "Nike Downshifter 14"

        session_row = _session(db, merchant)

        quote_contract, quote_row, _product, _variant = _quote_size9(db, session_row, merchant)
        assert quote_contract.total_minor == major_to_minor("4799")
        assert quote_contract.live_validated is True

        cart_contract, _cart_row = cart_service.create_cart(
            db,
            session_row.session_id,
            quote_row,
            CreateCartRequest(quote_id=quote_contract.quote_id),
        )
        assert cart_contract.total_minor == major_to_minor("4799")

        result = checkout_service.run_checkout(
            db,
            session_row,
            merchant,
            CheckoutRequest(quote_id=quote_contract.quote_id, idempotency_key=_idem_key()),
        )
        assert result["blocked"] is False, result
        assert result["status"] == TransactionStatus.PAYMENT_PENDING.value

        txn_ref = result["transaction_id"]
        order = result["payment_initiation"]

        done = checkout_service.complete_payment(
            db,
            session_row,
            merchant,
            txn_ref,
            order_id=order["order_id"],
            payment_id=f"pay_{order['order_id']}",
            signature="mock-signature",
        )
        assert done["status"] == TransactionStatus.COMPLETED.value, done
        assert done["shopify_reference"].startswith("mock-order-")

        txn = TransactionService(db).get_by_ref_or_404(txn_ref)
        events = [e.event_type for e in get_events(db, txn.id)]
        for expected in (
            "PRODUCT_SELECTED",
            "QUOTE_CREATED",
            "POLICY_EVALUATED",
            "AUTHORIZATION_GRANTED",
            "CART_CREATED",
            "PAYMENT_ORDER_CREATED",
            "PAYMENT_AUTHORIZED",
            "PAYMENT_CAPTURED",
            "TRANSACTION_COMPLETED",
        ):
            assert expected in events, f"missing {expected} in {events}"

        assert txn.authorized_amount == major_to_minor("4799")

    def test_checkout_is_idempotent(self, seeded):
        db, merchant = seeded
        session_row = _session(db, merchant)
        quote_contract, *_rest = _quote_size9(db, session_row, merchant)

        req = CheckoutRequest(quote_id=quote_contract.quote_id, idempotency_key="itest-idem-00001")
        first = checkout_service.run_checkout(db, session_row, merchant, req)
        second = checkout_service.run_checkout(db, session_row, merchant, req)

        assert first["blocked"] is False
        assert second.get("idempotent_replay") is True
        assert second["transaction_id"] == first["transaction_id"]
        assert second["payment_initiation"]["order_id"] == first["payment_initiation"]["order_id"]


class TestBlockedPaths:
    def test_price_change_after_quote_blocks(self, seeded):
        """PRD §25 mandatory failure demo at service level."""
        db, merchant = seeded
        session_row = _session(db, merchant)
        quote_contract, *_rest = _quote_size9(db, session_row, merchant)

        # Merchant silently raises the live price to ₹5,799 after our quote.
        db.add(
            DemoOverride(
                merchant_id=merchant.id,
                target_external_id="mock-var-ds-9-black",
                price_minor=major_to_minor("5799"),
                active=True,
                note="failure demo",
            )
        )
        db.commit()

        result = checkout_service.run_checkout(
            db,
            session_row,
            merchant,
            CheckoutRequest(quote_id=quote_contract.quote_id, idempotency_key=_idem_key()),
        )

        assert result["blocked"] is True
        assert "PRICE_CHANGED_SINCE_QUOTE" in result["reason_codes"]
        assert "FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION" in result["reason_codes"]
        assert "price changed" in result["explanation"].lower()

        txn = TransactionService(db).get_by_ref_or_404(result["transaction_id"])
        events = [e.event_type for e in get_events(db, txn.id)]
        assert "AUTHORIZATION_DENIED" in events
        assert "TRANSACTION_BLOCKED" in events
        assert "PAYMENT_ORDER_CREATED" not in events  # no money movement attempted

    def test_inventory_race_blocks(self, seeded):
        db, merchant = seeded
        session_row = _session(db, merchant)
        quote_contract, *_rest = _quote_size9(db, session_row, merchant)

        db.add(
            DemoOverride(
                merchant_id=merchant.id,
                target_external_id="mock-var-ds-9-black",
                available_for_sale=False,
                available_quantity=0,
                active=True,
            )
        )
        db.commit()

        result = checkout_service.run_checkout(
            db,
            session_row,
            merchant,
            CheckoutRequest(quote_id=quote_contract.quote_id, idempotency_key=_idem_key()),
        )
        assert result["blocked"] is True
        assert any(
            code in result["reason_codes"]
            for code in ("VARIANT_UNAVAILABLE", "INVENTORY_UNAVAILABLE")
        )

    def test_over_budget_product_blocked_by_buyer_limit(self, seeded):
        """Adidas Ultrabounce @ ₹5,499 > ₹5,000 buyer cap."""
        db, merchant = seeded
        session_row = _session(db, merchant)

        product, variants = catalog_service.get_product_or_404(db, merchant.id, "mock-prod-ultrabounce")
        quote_contract, *_rest = quote_service.create_quote(
            db,
            session_row,
            merchant,
            QuoteRequest(product_id=product.id, variant_id=variants[0].id, quantity=1),
        )
        assert quote_contract.total_minor == major_to_minor("5499")

        result = checkout_service.run_checkout(
            db,
            session_row,
            merchant,
            CheckoutRequest(quote_id=quote_contract.quote_id, idempotency_key=_idem_key()),
        )
        assert result["blocked"] is True
        assert "FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION" in result["reason_codes"]
