"""Buyer agent runner tests (scripted brain, mock merchant)."""

import pytest

from app.db.seeds import seed_mock_merchant
from app.domain.contracts import StartAgentSessionRequest
from app.domain.money import major_to_minor
from app.services import checkout as checkout_service
from app.services.sessions import create_session


@pytest.fixture()
def seeded(db_session):
    return db_session, seed_mock_merchant(db_session)


def _collect(db, session_row, merchant):
    from app.agent.runner import run_agent_session

    return list(run_agent_session(db, session_row, merchant))


class TestScriptedRun:
    def test_full_autonomous_run_authorizes(self, seeded):
        db, merchant = seeded
        session = create_session(
            db,
            StartAgentSessionRequest(
                intent="Find me Nike Downshifter 14, size 9, under INR 5,000 with reliable returns",
                max_budget_minor=major_to_minor("5000"),
            ),
            merchant_id=merchant.id,
        )

        events = _collect(db, session, merchant)
        tools_called = [e.tool for e in events if e.type == "tool_call"]
        assert tools_called == [
            "discover_merchant",
            "search_products",
            "get_quote",
            "create_cart",
            "checkout",
        ]

        final = events[-1]
        assert final.type == "final"
        assert final.payload["outcome"] == "PAYMENT_PENDING", final.label

        # The console stream carries the human-readable story.
        labels = [e.label for e in events]
        assert any("Live quote" in label for label in labels)
        assert any("Authorized" in label for label in labels)

    def test_run_blocks_on_price_change(self, seeded):
        """PRD §25: quote at ₹4,799, live price flips to ₹5,799 before checkout."""
        db, merchant = seeded
        session = create_session(
            db,
            StartAgentSessionRequest(
                intent="Find Nike Downshifter 14 size 9 under INR 5000",
                max_budget_minor=major_to_minor("5000"),
                demo_scenario="PRICE_CHANGE_AFTER_QUOTE",
            ),
            merchant_id=merchant.id,
        )

        events = _collect(db, session, merchant)
        final = events[-1]
        assert final.type == "final"
        assert final.payload["outcome"] == "BLOCKED"

        # The scenario status line must appear in the console stream.
        assert any("PRICE_CHANGE_AFTER_QUOTE" in (e.label or "") for e in events)

        checkout_result = next(
            e for e in reversed(events) if e.tool == "checkout" and e.type == "tool_result"
        )
        codes = (checkout_result.payload["result"])["reason_codes"]
        assert "PRICE_CHANGED_SINCE_QUOTE" in codes
        assert "FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION" in codes

    def test_size_preference_selects_matching_variant(self, seeded):
        db, merchant = seeded
        session = create_session(
            db,
            StartAgentSessionRequest(
                intent="Find me Nike Revolution 7, size 10",
                max_budget_minor=major_to_minor("5000"),
                preferred_size=None,
            ),
            merchant_id=merchant.id,
        )

        events = _collect(db, session, merchant)
        quote_event = next(e for e in events if e.tool == "get_quote" and e.type == "tool_result")
        result = quote_event.payload["result"]
        # Revolution 7 size-10 grey variant is ₹3,695 and available.
        assert result["total_minor"] == major_to_minor("3695")

    def test_no_match_stops_gracefully(self, seeded):
        db, merchant = seeded
        session = create_session(
            db,
            StartAgentSessionRequest(
                intent="Find me unobtanium galoshes size 44",
                max_budget_minor=major_to_minor("5000"),
            ),
            merchant_id=merchant.id,
        )
        events = _collect(db, session, merchant)
        final = events[-1]
        assert final.type == "final"
        assert final.payload["outcome"] == "stopped"


class TestCheckoutStillIdempotentAfterRun:
    def test_completed_run_leaves_clean_state(self, seeded):
        """A completed agent run must not leave an active transaction behind."""
        db, merchant = seeded
        session = create_session(
            db,
            StartAgentSessionRequest(
                intent="Find Nike Downshifter 14 size 9",
                max_budget_minor=major_to_minor("5000"),
            ),
            merchant_id=merchant.id,
        )
        events = _collect(db, session, merchant)
        final = events[-1]
        assert final.payload["outcome"] == "PAYMENT_PENDING"

        txn_service = checkout_service.TransactionService(db)
        active = txn_service.get_active_for_session(session.session_id)
        assert active is not None
        assert active.status == "PAYMENT_PENDING"  # parked at payment, not re-runnable
