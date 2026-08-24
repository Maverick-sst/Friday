"""Mock commerce adapter: a fixture "Shopify-like" store.

Purpose (PRD Phase 2): validate the whole gateway/policy/checkout abstraction
without any external dependency, and provide deterministic hooks for the
mandatory failure demo.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.commerce.base import (
    LiveVariantState,
    MerchantMetadata,
    SourceOrderResult,
    SyncResult,
)
from app.db.demo_overrides import DemoOverride
from app.db.models import Product, ProductVariant

# Fixture catalog mirrors what we seed into the canonical DB. The mock adapter
# treats canonical external_ids as the source of truth for live validation.
MOCK_CATALOG: list[dict[str, Any]] = [
    {
        "external_id": "mock-prod-downshifter",
        "title": "Nike Downshifter 14",
        "description": "Lightweight everyday running shoe with responsive cushioning.",
        "category": "running_shoes",
        "brand": "Nike",
        "product_url": "https://mock.velocity-sports.test/products/downshifter-14",
        "image_url": "https://mock.velocity-sports.test/images/downshifter-14.jpg",
        "variants": [
            {"external_id": "mock-var-ds-8-black", "options": {"size": "8", "color": "Black"},
             "price_minor": 479900, "quantity": 12},
            {"external_id": "mock-var-ds-9-black", "options": {"size": "9", "color": "Black"},
             "price_minor": 479900, "quantity": 8},
            {"external_id": "mock-var-ds-9-white", "options": {"size": "9", "color": "White"},
             "price_minor": 479900, "quantity": 0},
            {"external_id": "mock-var-ds-10-black", "options": {"size": "10", "color": "Black"},
             "price_minor": 479900, "quantity": 5},
        ],
    },
    {
        "external_id": "mock-prod-revolution",
        "title": "Nike Revolution 7",
        "description": "Soft, springy road feel for daily runs.",
        "category": "running_shoes",
        "brand": "Nike",
        "product_url": "https://mock.velocity-sports.test/products/revolution-7",
        "image_url": "https://mock.velocity-sports.test/images/revolution-7.jpg",
        "variants": [
            {"external_id": "mock-var-rv-9-black", "options": {"size": "9", "color": "Black"},
             "price_minor": 369500, "quantity": 15},
            {"external_id": "mock-var-rv-10-grey", "options": {"size": "10", "color": "Grey"},
             "price_minor": 369500, "quantity": 6},
        ],
    },
    {
        "external_id": "mock-prod-ultrabounce",
        "title": "Adidas Ultrabounce",
        "description": "Bouncy cushioning for effortless strides.",
        "category": "running_shoes",
        "brand": "Adidas",
        "product_url": "https://mock.velocity-sports.test/products/ultrabounce",
        "image_url": "https://mock.velocity-sports.test/images/ultrabounce.jpg",
        "variants": [
            {"external_id": "mock-var-ub-9-white", "options": {"size": "9", "color": "White"},
             "price_minor": 549900, "quantity": 4},
        ],
    },
]


def _apply_demo_override(
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
    state.raw["demo_override"] = {
        "id": override.id,
        "note": override.note,
    }
    return state


class MockAdapter:
    provider = "mock"

    def __init__(self) -> None:
        self._counter = 0

    # -- CommerceAdapter surface -------------------------------------------------

    def fetch_merchant_metadata(self, store_ref: str) -> MerchantMetadata:
        return MerchantMetadata(
            name="Velocity Sports",
            slug="velocity-sports",
            description="Online sportswear and running equipment store.",
            category="sportswear",
            website_url=store_ref or "https://velocity-sports.test",
            logo_url=None,
            currency="INR",
        )

    def sync_catalog(self, db: Session, merchant_id: str) -> SyncResult:
        result = SyncResult()
        for prod in MOCK_CATALOG:
            row = db.scalar(
                select(Product).where(
                    Product.merchant_id == merchant_id,
                    Product.external_id == prod["external_id"],
                )
            )
            if row is None:
                fields = {k: v for k, v in prod.items() if k != "variants"}
                row = Product(merchant_id=merchant_id, source="mock", **fields)
                db.add(row)
                db.flush()
            else:
                for key in ("title", "description", "category", "brand", "product_url", "image_url"):
                    setattr(row, key, prod[key])
            result.products_synced += 1

            existing = {v.external_id: v for v in row.variants}
            for var in prod["variants"]:
                variant = existing.get(var["external_id"])
                if variant is None:
                    variant = ProductVariant(product_id=row.id, currency="INR")
                    db.add(variant)
                    db.flush()
                variant.external_id = var["external_id"]
                variant.options_json = dict(var["options"])
                variant.price = var["price_minor"]
                variant.available_quantity = var["quantity"]
                variant.available_for_sale = var["quantity"] > 0
                variant.sku = f"VS-{var['external_id'][-8:].upper()}"
                option_values = [str(v) for v in var["options"].values()]
                variant.title = " / ".join(option_values)
                result.variants_synced += 1
        return result

    def live_validate_variant(
        self, db: Session, merchant_id: str, variant_external_id: str
    ) -> LiveVariantState:
        row = db.scalar(
            select(ProductVariant).where(ProductVariant.external_id == variant_external_id)
        )
        if row is None:
            raise LookupError(f"variant not found at source: {variant_external_id}")

        product = db.get(Product, row.product_id)
        state = LiveVariantState(
            external_variant_id=row.external_id,
            external_product_id=product.external_id if product else None,
            price_minor=row.price,
            currency=row.currency,
            available_for_sale=row.available_for_sale and (row.available_quantity or 0) > 0,
            available_quantity=row.available_quantity,
            raw={"provider": "mock"},
        )
        return _apply_demo_override(db, merchant_id, variant_external_id, state)

    def create_source_order(self, db: Session, merchant_id: str, order: dict[str, Any]) -> SourceOrderResult:
        self._counter += 1
        return SourceOrderResult(reference=f"mock-order-{self._counter:06d}", raw={"status": "created"})
