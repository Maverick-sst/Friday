"""Seed data: the mock "Velocity Sports" merchant for instant demos."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.commerce.mock import MockAdapter
from app.db.models import (
    Merchant,
    MerchantCapability,
    MerchantIntegration,
    MerchantPolicy,
)
from app.domain.enums import Capability


def seed_mock_merchant(db: Session) -> Merchant:
    """Create Velocity Sports + capabilities + policy + catalog. Idempotent."""
    existing = db.scalar(select(Merchant).where(Merchant.slug == "velocity-sports"))
    if existing is not None:
        return existing

    merchant = Merchant(
        name="Velocity Sports",
        slug="velocity-sports",
        description="Online sportswear and running equipment store.",
        category="sportswear",
        subcategory="running_shoes",
        website_url="https://velocity-sports.test",
        status="active",
    )
    db.add(merchant)
    db.flush()

    db.add(
        MerchantIntegration(
            merchant_id=merchant.id,
            provider="mock",
            store_url="https://velocity-sports.test",
            scopes_json=["read_products", "read_inventory", "write_draft_orders"],
            status="connected",
        )
    )

    for cap in Capability:
        db.add(
            MerchantCapability(
                merchant_id=merchant.id,
                capability_name=cap.value,
                enabled=True,
                version=1,
                config_json={},
            )
        )

    db.add(
        MerchantPolicy(
            merchant_id=merchant.id,
            max_auto_purchase=500000,  # ₹5,000 in paise
            approval_threshold=500000,  # human approval required above ₹5,000
            allowed_categories_json=["running_shoes", "sportswear"],
            allowed_regions_json=[],
            currency="INR",
            return_window_days=7,
            allow_cancellation=True,
            version=1,
        )
    )
    db.flush()

    adapter = MockAdapter()
    adapter.sync_catalog(db, merchant.id)
    db.commit()
    return merchant
