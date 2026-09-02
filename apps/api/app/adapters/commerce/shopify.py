"""Shopify commerce adapter (PRD §17).

Talks to the Shopify Admin GraphQL API (version 2026-07 verified against
shopify.dev at implementation time). Access tokens never leave the server.

External ids are stored as full GIDs, e.g. `gid://shopify/ProductVariant/123`.
"""

import logging
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.commerce.base import (
    LiveVariantState,
    MerchantMetadata,
    SourceOrderResult,
    SyncResult,
)
from app.adapters.demo_support import apply_demo_override
from app.core.config import get_settings
from app.core.errors import GatewayError
from app.core.security import decrypt_secret
from app.db.models import MerchantIntegration
from app.domain.money import major_to_minor

logger = logging.getLogger("acg.shopify")


class ShopifyApiError(Exception):
    pass


class ShopifyAdapter:
    provider = "shopify"

    # -- plumbing -----------------------------------------------------------------

    def _token(self, db: Session, merchant_id: str) -> str:
        integration = db.scalar(
            select(MerchantIntegration).where(
                MerchantIntegration.merchant_id == merchant_id,
                MerchantIntegration.provider == "shopify",
            )
        )
        if integration is None or not integration.auth_reference_encrypted:
            raise GatewayError("SHOPIFY_NOT_CONNECTED", "No Shopify token stored for merchant", 409)
        return decrypt_secret(integration.auth_reference_encrypted)

    def _store_host(self, db: Session, merchant_id: str) -> str:
        integration = db.scalar(
            select(MerchantIntegration).where(
                MerchantIntegration.merchant_id == merchant_id,
                MerchantIntegration.provider == "shopify",
            )
        )
        if integration is None or not integration.store_url:
            raise GatewayError("SHOPIFY_NOT_CONNECTED", "No Shopify store bound to merchant", 409)
        return integration.store_url.replace("https://", "").strip("/")

    def _graphql(
        self, db: Session, merchant_id: str, query: str, variables: dict | None = None
    ) -> dict[str, Any]:
        settings = get_settings()
        host = self._store_host(db, merchant_id)
        url = f"https://{host}/admin/api/{settings.shopify_api_version}/graphql.json"
        headers = {
            "X-Shopify-Access-Token": self._token(db, merchant_id),
            "Content-Type": "application/json",
        }
        response = httpx.post(
            url, json={"query": query, "variables": variables or {}}, headers=headers, timeout=25
        )
        if response.status_code != 200:
            raise ShopifyApiError(f"Shopify HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        errors = payload.get("errors")
        data: dict[str, Any] = payload.get("data") or {}
        if data.get("productVariant") is None and variables and "id" in (variables or {}):
            # Missing objects return null data alongside GraphQL errors.
            raise LookupError(f"variant not found at Shopify: {variables['id']}")
        if errors:
            raise ShopifyApiError(f"Shopify GraphQL errors: {errors}")
        return data

    @staticmethod
    def _money_minor(money: dict) -> tuple[int, str]:
        amount = Decimal(str(money["amount"]))
        return major_to_minor(amount), money.get("currencyCode", "INR")

    # -- CommerceAdapter surface ---------------------------------------------------

    def fetch_merchant_metadata_with_token(self, shop_host: str, access_token: str) -> MerchantMetadata:
        """Bootstrap variant used during OAuth before a DB credential exists."""
        settings = get_settings()
        url = f"https://{shop_host}/admin/api/{settings.shopify_api_version}/graphql.json"
        query = """
        query {
          shop { name description currencyCode primaryDomain { url } }
        }
        """
        response = httpx.post(
            url,
            json={"query": query},
            headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
            timeout=25,
        )
        if response.status_code != 200:
            raise ShopifyApiError(f"Shopify HTTP {response.status_code}: {response.text[:300]}")
        shop = (response.json().get("data") or {}).get("shop") or {}
        slug = shop_host.split(".")[0]
        return MerchantMetadata(
            name=shop.get("name") or slug,
            slug=slug,
            description=(shop.get("description") or None),
            category=None,
            website_url=((shop.get("primaryDomain") or {}).get("url")) or f"https://{shop_host}",
            logo_url=None,
            currency=shop.get("currencyCode") or "INR",
        )

    def fetch_merchant_metadata(self, store_ref: str) -> MerchantMetadata:  # pragma: no cover
        raise NotImplementedError("use fetch_merchant_metadata_with_token during OAuth bootstrap")

    def sync_catalog(self, db: Session, merchant_id: str) -> SyncResult:

        result = SyncResult()
        cursor: str | None = None
        while True:
            variables: dict[str, Any] = {"cursor": cursor}
            data = self._graphql(db, merchant_id, _PRODUCTS_PAGE_QUERY, variables)
            products = ((data.get("products") or {}).get("nodes")) or []
            for prod in products:
                self._upsert_product(db, merchant_id, prod)
                result.products_synced += 1
                result.variants_synced += len((prod.get("variants") or {}).get("nodes") or [])
            page_info = ((data.get("products") or {}).get("pageInfo")) or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        db.commit()
        logger.info(
            "shopify sync complete",
            extra={
                "extra_fields": {
                    "merchant": merchant_id,
                    "products": result.products_synced,
                    "variants": result.variants_synced,
                }
            },
        )
        return result

    def _upsert_product(self, db: Session, merchant_id: str, prod: dict) -> None:
        from datetime import UTC, datetime

        from app.db.models import Product, ProductVariant

        external_id = prod["id"]
        row = db.scalar(
            select(Product).where(Product.merchant_id == merchant_id, Product.external_id == external_id)
        )
        media = (prod.get("featuredMedia") or {}).get("preview", {}).get("image", {})
        values = {
            "title": prod.get("title"),
            "description": prod.get("descriptionHtml"),
            "category": prod.get("productType") or None,
            "brand": prod.get("vendor"),
            "product_url": prod.get("onlineStoreUrl"),
            "image_url": media.get("url"),
            "status": (prod.get("status") or "ACTIVE").lower(),
            "source_updated_at": datetime.now(UTC),
        }
        if row is None:
            row = Product(merchant_id=merchant_id, external_id=external_id, source="shopify", **values)
            db.add(row)
            db.flush()
        else:
            for key, val in values.items():
                setattr(row, key, val)

        existing = {
            v.external_id: v
            for v in db.scalars(select(ProductVariant).where(ProductVariant.product_id == row.id))
        }
        variants = (prod.get("variants") or {}).get("nodes") or []
        for var in variants:
            price_minor, currency = self._money_minor(var["price"])
            options = {o["name"].lower(): o["value"] for o in var.get("selectedOptions") or []}
            vrow = existing.get(var["id"])
            if vrow is None:
                vrow = ProductVariant(product_id=row.id)
                db.add(vrow)
                db.flush()
            vrow.external_id = var["id"]
            vrow.sku = var.get("sku")
            vrow.title = var.get("title")
            vrow.price = price_minor
            vrow.currency = currency
            vrow.available_quantity = var.get("inventoryQuantity")
            vrow.available_for_sale = bool(var.get("availableForSale"))
            vrow.options_json = options
            vrow.source_updated_at = values["source_updated_at"]

    def live_validate_variant(
        self, db: Session, merchant_id: str, variant_external_id: str
    ) -> LiveVariantState:
        data = self._graphql(
            db,
            merchant_id,
            _VARIANT_LIVE_QUERY,
            {"id": variant_external_id},
        )
        var = data.get("productVariant")
        if var is None:
            raise LookupError(f"variant not found at Shopify: {variant_external_id}")
        price_minor, currency = self._money_minor(var["price"])
        state = LiveVariantState(
            external_variant_id=var["id"],
            external_product_id=(var.get("product") or {}).get("id"),
            price_minor=price_minor,
            currency=currency,
            available_for_sale=bool(var.get("availableForSale")),
            available_quantity=var.get("inventoryQuantity"),
            raw={"provider": "shopify", "sku": var.get("sku")},
        )
        return apply_demo_override(db, merchant_id, variant_external_id, state)

    def create_source_order(self, db: Session, merchant_id: str, order: dict[str, Any]) -> SourceOrderResult:
        """Push a completed purchase as a Shopify draft order."""
        data = self._graphql(
            db,
            merchant_id,
            _DRAFT_ORDER_CREATE_MUTATION,
            {
                "input": {
                    "lineItems": [{"variantId": order["variant_gid"], "quantity": order.get("quantity", 1)}],
                    "note": f"Agent Commerce txn {order.get('txn_ref')}",
                    "tags": ["agent-commerce"],
                }
            },
        )
        payload = data.get("draftOrderCreate") or {}
        user_errors = payload.get("userErrors") or []
        if user_errors:
            raise ShopifyApiError(f"draftOrderCreate userErrors: {user_errors}")
        draft = payload.get("draftOrder") or {}
        return SourceOrderResult(reference=draft.get("id"), raw=draft)


_PRODUCTS_PAGE_QUERY = """
query Products($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      descriptionHtml
      productType
      vendor
      status
      onlineStoreUrl
      featuredMedia { preview { image { url } } }
      variants(first: 100) {
        nodes {
          id
          title
          sku
          price { amount currencyCode }
          availableForSale
          inventoryQuantity
          selectedOptions { name value }
        }
      }
    }
  }
}
"""

_VARIANT_LIVE_QUERY = """
query Variant($id: ID!) {
  productVariant(id: $id) {
    id
    sku
    title
    price { amount currencyCode }
    availableForSale
    inventoryQuantity
    product { id }
  }
}
"""

_DRAFT_ORDER_CREATE_MUTATION = """
mutation DraftOrderCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name invoiceUrl status }
    userErrors { field message }
  }
}
"""
