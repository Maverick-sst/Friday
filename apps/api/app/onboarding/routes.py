"""Onboarding routes: connect flow, sync, profile, policies, capabilities.

V0 supports the mock provider (instant demo seed) and the Shopify OAuth
connect flow (Phase 4 fills in the adapter implementation).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import get_commerce_adapter
from app.core.config import get_settings
from app.core.errors import GatewayError, not_found
from app.db.models import (
    Merchant,
    MerchantIntegration,
    MerchantPolicy,
    Product,
)
from app.db.seeds import seed_mock_merchant
from app.db.session import get_db
from app.domain.enums import Capability as CapabilityEnum
from app.services.profile_builder import build_profile

router = APIRouter(prefix="/api/v1", tags=["onboarding"])

V0_CAPABILITY_NAMES = [c.value for c in CapabilityEnum]


class ConnectStoreRequest(BaseModel):
    store_url: str = Field(min_length=4, max_length=255)


class UpdatePoliciesRequest(BaseModel):
    max_auto_purchase_minor: int | None = Field(default=None, ge=0)
    approval_threshold_minor: int | None = Field(default=None, ge=0)
    allowed_categories: list[str] | None = None
    return_window_days: int | None = Field(default=None, ge=0, le=365)
    allow_cancellation: bool | None = None


@router.get("/merchants")
def list_merchants(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Merchant).order_by(Merchant.created_at)))
    return {
        "merchants": [
            {
                "id": m.slug,
                "name": m.name,
                "status": m.status,
                "category": m.category,
                "website_url": m.website_url,
            }
            for m in rows
        ]
    }


@router.get("/merchants/{merchant_id}/profile")
def get_profile(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    integration = db.scalar(
        select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant.id)
    )
    profile = build_profile(db, merchant, integration=integration)
    payload = profile.model_dump()
    payload["storefront_status"] = merchant.status
    payload["sync"] = {
        "provider": integration.provider if integration else None,
        "last_synced_at": (
            integration.last_synced_at.isoformat() if integration and integration.last_synced_at else None
        ),
        "product_count": _product_count(db, merchant.id),
    }
    return payload


@router.post("/onboarding/demo-seed")
def demo_seed(db: Session = Depends(get_db)):
    """One-click mock merchant for demos/development."""
    existing = db.scalar(select(Merchant).where(Merchant.slug == "velocity-sports"))
    if existing is not None:
        return {"merchant_id": existing.slug, "created": False}
    merchant = seed_mock_merchant(db)
    return {"merchant_id": merchant.slug, "created": True}


@router.post("/onboarding/shopify/connect")
def shopify_connect(req: ConnectStoreRequest, db: Session = Depends(get_db)):
    """Begin the Shopify OAuth install flow. Returns the authorize redirect URL."""
    settings = get_settings()
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise GatewayError(
            "SHOPIFY_NOT_CONFIGURED",
            "Shopify app credentials are not configured on this server",
            status_code=501,
        )
    from app.onboarding.shopify_oauth import build_authorize_url, normalize_store_host

    host = normalize_store_host(req.store_url)
    url, nonce = build_authorize_url(host)
    return {"shop": host, "authorize_url": url, "state": nonce}


@router.post("/merchants/{merchant_id}/sync")
def sync_merchant(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    integration = db.scalar(
        select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant.id)
    )
    if integration is None:
        raise not_found("INTEGRATION_MISSING", "Merchant has no commerce integration connected")

    from datetime import UTC, datetime

    adapter = get_commerce_adapter(integration.provider)
    result = adapter.sync_catalog(db, merchant.id)
    integration.last_synced_at = datetime.now(UTC)
    db.commit()
    return {
        "merchant_id": merchant.slug,
        "provider": adapter.provider,
        "products_synced": result.products_synced,
        "variants_synced": result.variants_synced,
    }


@router.put("/merchants/{merchant_id}/policies")
def update_policies(merchant_id: str, req: UpdatePoliciesRequest, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    policy = db.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == merchant.id))
    if policy is None:
        raise not_found("POLICY_MISSING", "Merchant has no policies configured")

    if req.max_auto_purchase_minor is not None:
        policy.max_auto_purchase = req.max_auto_purchase_minor
    if req.approval_threshold_minor is not None:
        policy.approval_threshold = req.approval_threshold_minor
    if req.allowed_categories is not None:
        policy.allowed_categories_json = req.allowed_categories
    if req.return_window_days is not None:
        policy.return_window_days = req.return_window_days
    if req.allow_cancellation is not None:
        policy.allow_cancellation = req.allow_cancellation
    policy.version += 1
    db.commit()

    return {
        "max_auto_purchase_minor": policy.max_auto_purchase,
        "approval_threshold_minor": policy.approval_threshold,
        "allowed_categories": policy.allowed_categories_json,
        "currency": policy.currency,
        "return_window_days": policy.return_window_days,
        "allow_cancellation": policy.allow_cancellation,
        "version": policy.version,
    }


def _merchant_or_404(db: Session, slug: str) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.slug == slug))
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", f"No merchant {slug}")
    return merchant


def _product_count(db: Session, merchant_id: str) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Product).where(Product.merchant_id == merchant_id)
    return int(db.scalar(stmt) or 0)
