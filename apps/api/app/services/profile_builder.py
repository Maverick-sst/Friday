"""Builds the canonical Merchant Agent Profile from DB rows (PRD §10)."""


from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Merchant,
    MerchantCapability,
    MerchantIntegration,
    MerchantPolicy,
)
from app.domain.contracts import (
    MerchantAgentProfile,
    MerchantCommerce,
    MerchantIdentity,
    MerchantPolicyView,
    MerchantSourceInfo,
)
from app.domain.enums import Capability


def build_profile(
    db: Session,
    merchant: Merchant,
    policy: MerchantPolicy | None = None,
    capabilities: list[MerchantCapability] | None = None,
    integration: MerchantIntegration | None = None,
) -> MerchantAgentProfile:
    if policy is None:
        policy = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
    if capabilities is None:
        capabilities = list(
            db.scalars(select(MerchantCapability).where(MerchantCapability.merchant_id == merchant.id))
        )

    enabled: list[Capability] = []
    for row in capabilities or []:
        try:
            cap = Capability(row.capability_name)
        except ValueError:
            continue
        if row.enabled:
            enabled.append(cap)

    return MerchantAgentProfile(
        merchant=MerchantIdentity(
            id=merchant.slug,
            name=merchant.name,
            description=merchant.description,
            category=merchant.category,
            subcategories=[s for s in [merchant.subcategory] if s],
            website=merchant.website_url,
            logo_url=merchant.logo_url,
        ),
        commerce=MerchantCommerce(
            currency=policy.currency if policy else "INR",
            capabilities=enabled or [c for c in Capability],
        ),
        policies=MerchantPolicyView(
            max_auto_purchase_minor=policy.max_auto_purchase if policy else 0,
            approval_threshold_minor=policy.approval_threshold if policy else 0,
            allowed_categories=(policy.allowed_categories_json or []) if policy else [],
            currency=policy.currency if policy else "INR",
            return_window_days=policy.return_window_days if policy else 0,
            allow_cancellation=policy.allow_cancellation if policy else True,
        ),
        source=MerchantSourceInfo(
            provider=integration.provider if integration else "shopify",
            store_url=integration.store_url if integration else merchant.website_url,
            last_synced_at=integration.last_synced_at if integration else None,
        ),
    )
