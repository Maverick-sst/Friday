"""Shared live-state mutation hook for failure demos.

Both adapters consult DemoOverride rows before returning "live" state, so the
dashboard can deterministically force a price change or inventory race
regardless of whether the source platform is the mock store or real Shopify.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.commerce.base import LiveVariantState
from app.db.demo_overrides import DemoOverride


def apply_demo_override(
    db: Session, merchant_id: str, external_variant_id: str, state: LiveVariantState
) -> LiveVariantState:
    override = db.scalar(
        select(DemoOverride).where(
            DemoOverride.merchant_id == merchant_id,
            DemoOverride.target_external_id == external_variant_id,
            DemoOverride.active.is_(True),
        )
    )
    if override is None:
        return state
    price, available, quantity = override.apply(
        state.price_minor, state.available_for_sale, state.available_quantity
    )
    state.price_minor = price
    state.available_for_sale = available
    state.available_quantity = quantity
    state.raw["demo_override"] = {"id": override.id, "note": override.note}
    return state
