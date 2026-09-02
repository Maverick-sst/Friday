"""Shopify connect service: OAuth callback handling and merchant upsert."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.commerce.shopify import ShopifyAdapter
from app.core.config import get_settings
from app.core.errors import GatewayError
from app.core.security import encrypt_secret
from app.db.models import Merchant, MerchantCapability, MerchantIntegration, MerchantPolicy
from app.domain.enums import Capability

logger = logging.getLogger("acg.onboarding")

V0_DEFAULT_POLICY = {
    "max_auto_purchase": 500000,  # ₹5,000
    "approval_threshold": 500000,
    "allowed_categories_json": [],
    "currency": "INR",
    "return_window_days": 7,
}


def upsert_shopify_merchant(db: Session, shop_host: str, access_token: str) -> Merchant:
    """Create/refresh the merchant from Shopify metadata and store the token."""
    adapter = ShopifyAdapter()
    metadata = adapter.fetch_merchant_metadata_with_token(shop_host, access_token)

    integration = db.scalar(
        select(MerchantIntegration).where(
            MerchantIntegration.provider == "shopify", MerchantIntegration.store_url == f"https://{shop_host}"
        )
    )
    merchant = None
    if integration is not None:
        merchant = db.get(Merchant, integration.merchant_id)

    if merchant is None:
        slug = _unique_slug(db, metadata.slug)
        merchant = Merchant(
            name=metadata.name,
            slug=slug,
            description=metadata.description,
            category=metadata.category,
            website_url=metadata.website_url,
            logo_url=metadata.logo_url,
            status="pending",
        )
        db.add(merchant)
        db.flush()
        integration = MerchantIntegration(merchant_id=merchant.id, provider="shopify")
        db.add(integration)
    else:
        merchant.name = metadata.name or merchant.name
        merchant.description = metadata.description or merchant.description
        merchant.website_url = metadata.website_url or merchant.website_url
        if merchant.status == "pending":
            merchant.status = "active"

    integration.store_url = f"https://{shop_host}"
    integration.auth_reference_encrypted = encrypt_secret(access_token)
    settings = get_settings()
    integration.scopes_json = settings.shopify_scope_list
    integration.status = "connected"

    # Default capability set + policy for newly connected merchants.
    existing_caps = list(
        db.scalars(select(MerchantCapability).where(MerchantCapability.merchant_id == merchant.id))
    )
    if not existing_caps:
        for cap in Capability:
            db.add(
                MerchantCapability(
                    merchant_id=merchant.id, capability_name=cap.value, enabled=True, version=1
                )
            )

    existing_policy = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
    if existing_policy is None:
        db.add(MerchantPolicy(merchant_id=merchant.id, **V0_DEFAULT_POLICY))

    db.flush()
    return merchant


def _unique_slug(db: Session, base: str) -> str:
    candidate = base
    suffix = 1
    while db.scalar(select(Merchant).where(Merchant.slug == candidate)) is not None:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def mark_synced(db: Session, merchant_id: str) -> None:
    integration = db.scalar(select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant_id))
    if integration is not None:
        integration.last_synced_at = datetime.now(UTC)
        db.commit()


__all__ = ["GatewayError", "mark_synced", "upsert_shopify_merchant"]
