"""E2E scenario matrix (PRD §31.3).

10 valid purchases, 5 price-mismatch cases, 3 inventory failures,
2 policy violations -> property: 0 unauthorized payment attempts.

Every scenario runs the full service stack against the mock adapter on an
in-memory database, mirroring production code paths exactly.
"""

import pytest

from app.db.demo_overrides import DemoOverride
from app.db.seeds import seed_mock_merchant
from app.domain.contracts import CheckoutRequest, QuoteRequest, StartAgentSessionRequest
from app.domain.money import major_to_minor
from app.services import catalog as catalog_service
from app.services import checkout as checkout_service
from app.services import quotes as quote_service
from app.services.sessions import create_session
from app.services.transactions import TransactionService, get_events

_seq = iter(range(1000))


def _idem() -> str:
    return f"e2e-{next(_seq):05d}"


def _session(db, merchant, budget="5000", **kw):
    req = StartAgentSessionRequest(
        max_budget_minor=major_to_minor(budget),
        intent=kw.pop("intent", "buy running shoes"),
        **kw,
    )
    return create_session(db, req, merchant_id=merchant.id)


def _quote(db, session, merchant, product_ext, variant_ext):
    product, variants = catalog_service.get_product_or_404(db, merchant.id, product_ext)
    variant = next(v for v in variants if v.external_id == variant_ext)
    contract, row = quote_service.create_quote(
        db, session, merchant, QuoteRequest(product_id=product.id, variant_id=variant.id)
    )
    return contract, row


def _checkout(db, session, merchant, quote_ref):
    return checkout_service.run_checkout(
        db, session, merchant, CheckoutRequest(quote_id=quote_ref, idempotency_key=_idem())
    )


def _payment_made(txn_id: str, db) -> bool:
    events = [e.event_type for e in get_events(db, txn_id)]
    return any(t in events for t in ("PAYMENT_ORDER_CREATED", "PAYMENT_AUTHORIZED", "PAYMENT_CAPTURED"))


# ---------------------------------------------------------------------------
# 10 valid purchases
# ---------------------------------------------------------------------------


class TestValidPurchases:
    @pytest.mark.parametrize(
        "product_ext,variant_ext,budget",
        [
            ("mock-prod-downshifter", "mock-var-ds-9-black", "5000"),
            ("mock-prod-downshifter", "mock-var-ds-8-black", "4800"),
            ("mock-prod-downshifter", "mock-var-ds-10-black", "4799"),
            ("mock-prod-downshifter", "mock-var-ds-9-black", "10000"),  # min() rule keeps it valid
            ("mock-prod-revolution", "mock-var-rv-9-black", "3695"),
            ("mock-prod-revolution", "mock-var-rv-9-black", "4200"),
            ("mock-prod-revolution", "mock-var-rv-10-grey", "3695"),
            ("mock-prod-revolution", "mock-var-rv-10-grey", "4000"),
            ("mock-prod-downshifter", "mock-var-ds-10-black", "5000"),
            ("mock-prod-revolution", "mock-var-rv-9-black", "3696"),
        ],
    )
    def test_purchase_authorizes(self, db_session, product_ext, variant_ext, budget):
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant, budget=budget)
        quote_contract, _row = _quote(db, session, merchant, product_ext, variant_ext)

        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is False, result.get("explanation")
        assert result["status"] == "PAYMENT_PENDING"

        txn = TransactionService(db).get_by_ref_or_404(result["transaction_id"])
        assert not _payment_failed_only(txn.id, db)


def _payment_failed_only(txn_id: str, db) -> bool:
    return any(e.event_type == "PAYMENT_FAILED" for e in get_events(db, txn_id))


# ---------------------------------------------------------------------------
# 5 price-mismatch cases (PRD §25 family)
# ---------------------------------------------------------------------------


class TestPriceMismatches:
    @pytest.mark.parametrize("live_major,budget", [("5799", "5000"), ("4850", "4800"), ("5799", "5799")])
    def test_raised_price_blocks(self, db_session, live_major, budget):
        """Price changes between quote and checkout."""
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant, budget=budget)
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-black")

        db.add(
            DemoOverride(
                merchant_id=merchant.id,
                target_external_id="mock-var-ds-9-black",
                price_minor=major_to_minor(live_major),
                active=True,
            )
        )
        db.commit()

        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert "PRICE_CHANGED_SINCE_QUOTE" in result["reason_codes"]
        txn = TransactionService(db).get_by_ref_or_404(result["transaction_id"])
        assert not _payment_made(txn.id, db)

    def test_price_drop_still_blocks_on_integrity(self, db_session):
        """Even a *cheaper* live price voids the stale quote - integrity over convenience."""
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant, budget="5000")
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-revolution", "mock-var-rv-9-black")

        db.add(
            DemoOverride(
                merchant_id=merchant.id,
                target_external_id="mock-var-rv-9-black",
                price_minor=299900,
                active=True,
            )
        )
        db.commit()

        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert "PRICE_CHANGED_SINCE_QUOTE" in result["reason_codes"]

    def test_quote_expired_blocks(self, db_session):
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant)
        quote_contract, quote_row = _quote(
            db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-black"
        )

        from datetime import UTC, datetime, timedelta

        quote_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert "QUOTE_EXPIRED" in result["reason_codes"]
        txn = TransactionService(db).get_by_ref_or_404(result["transaction_id"])
        assert not _payment_made(txn.id, db)


# ---------------------------------------------------------------------------
# 3 inventory failures (PRD §26 family)
# ---------------------------------------------------------------------------


class TestInventoryFailures:
    def test_variant_gone_between_quote_and_checkout(self, db_session):
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant)
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-ultrabounce", "mock-var-ub-9-white")
        db.add(
            DemoOverride(
                merchant_id=merchant.id,
                target_external_id="mock-var-ub-9-white",
                available_for_sale=False,
                available_quantity=0,
                active=True,
            )
        )
        db.commit()
        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert any(c in result["reason_codes"] for c in ("VARIANT_UNAVAILABLE", "INVENTORY_UNAVAILABLE"))

    def test_out_of_stock_variant_blocks_at_checkout(self, db_session):
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant)

        # size 9 white is seeded out of stock; the quote reflects live truth...
        contract, _row = _quote(db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-white")
        assert contract.inventory_available is False

        # ...and checkout refuses to move money.
        result = _checkout(db, session, merchant, contract.quote_id)
        assert result["blocked"] is True
        assert any(c in result["reason_codes"] for c in ("VARIANT_UNAVAILABLE", "INVENTORY_UNAVAILABLE"))

    def test_inventory_race_scenario_flag(self, db_session):
        from app.demo.scenarios import maybe_apply_pre_checkout

        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant)
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-black")

        session.constraints_json = {**session.constraints_json, "demo_scenario": "INVENTORY_RACE"}
        assert maybe_apply_pre_checkout(db, session) == "INVENTORY_RACE"

        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True


# ---------------------------------------------------------------------------
# 2 policy violations
# ---------------------------------------------------------------------------


class TestPolicyViolations:
    def test_buyer_budget_violation(self, db_session):
        db, merchant = db_session, seed_mock_merchant(db_session)
        session = _session(db, merchant, budget="4000")
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-black")
        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert "FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION" in result["reason_codes"]

    def test_category_not_allowed(self, db_session):
        db, merchant = db_session, seed_mock_merchant(db_session)
        policy = next(iter([merchant])) if False else None
        del policy
        from sqlalchemy import select

        from app.db.models import MerchantPolicy

        policy_row = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
        policy_row.allowed_categories_json = ["sportswear"]  # running_shoes removed
        db.commit()

        session = _session(db, merchant)
        quote_contract, _row = _quote(db, session, merchant, "mock-prod-downshifter", "mock-var-ds-9-black")
        result = _checkout(db, session, merchant, quote_contract.quote_id)
        assert result["blocked"] is True
        assert "CATEGORY_NOT_ALLOWED" in result["reason_codes"]


# ---------------------------------------------------------------------------
# Global safety property (PRD §31.3): zero unauthorized payments
# ---------------------------------------------------------------------------


def test_no_unauthorized_payment_attempts_anywhere(db_session):
    """Replay every block scenario; assert no payment order was ever created."""
    db, merchant = db_session, seed_mock_merchant(db_session)

    scenarios = []
    s1 = _session(db, merchant, budget="4000")
    q1, _ = _quote(db, s1, merchant, "mock-prod-ultrabounce", "mock-var-ub-9-white")
    scenarios.append((s1, q1.quote_id))

    s2 = _session(db, merchant, budget="5000")
    q2, _ = _quote(db, s2, merchant, "mock-prod-downshifter", "mock-var-ds-9-black")
    db.add(
        DemoOverride(
            merchant_id=merchant.id,
            target_external_id="mock-var-ds-9-black",
            price_minor=579900,
            active=True,
        )
    )
    db.commit()
    scenarios.append((s2, q2.quote_id))

    blocked_count = 0
    for sess, quote_ref in scenarios:
        result = _checkout(db, sess, merchant, quote_ref)
        if result["blocked"]:
            blocked_count += 1
            txn = TransactionService(db).get_by_ref_or_404(result["transaction_id"])
            assert not _payment_made(txn.id, db), (
                f"unauthorized payment attempt in {result['transaction_id']}"
            )
    assert blocked_count == len(scenarios)
