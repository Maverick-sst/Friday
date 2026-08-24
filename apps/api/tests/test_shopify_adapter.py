"""Shopify adapter + OAuth tests with a mocked GraphQL transport."""


import json

import httpx
import pytest

from app.adapters.commerce.shopify import ShopifyAdapter
from app.core.config import get_settings
from app.onboarding.shopify_connect import upsert_shopify_merchant
from app.onboarding.shopify_oauth import (
    build_authorize_url,
    normalize_store_host,
    read_state,
    verify_callback_hmac,
)


def json_module_dumps(value):
    return json.dumps(value or {}).encode()


def _shopify_payload(query: str, variables: dict) -> dict:
    if "query {" in query and "shop {" in query:
        return {
            "data": {
                "shop": {
                    "name": "Real Velocity",
                    "description": "A real store",
                    "currencyCode": "INR",
                    "primaryDomain": {"url": "https://real-velocity.myshopify.com"},
                }
            }
        }
    if "$cursor" in query or query.strip().startswith("query Products"):
        return {
            "data": {
                "products": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "gid://shopify/Product/111",
                            "title": "Nike Downshifter 14",
                            "descriptionHtml": "<p>Run</p>",
                            "productType": "Running Shoes",
                            "vendor": "Nike",
                            "status": "ACTIVE",
                            "onlineStoreUrl": None,
                            "featuredMedia": {
                                "preview": {"image": {"url": "https://cdn/img.jpg"}}
                            },
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/9009",
                                        "title": "9 / Black",
                                        "sku": "ND14-9-BLK",
                                        "price": {"amount": "47.99", "currencyCode": "USD"},
                                        "availableForSale": True,
                                        "inventoryQuantity": 8,
                                        "selectedOptions": [
                                            {"name": "Size", "value": "9"},
                                            {"name": "Color", "value": "Black"},
                                        ],
                                    },
                                    {
                                        "id": "gid://shopify/ProductVariant/9010",
                                        "title": "10 / White",
                                        "sku": None,
                                        "price": {"amount": "47.99", "currencyCode": "USD"},
                                        "availableForSale": False,
                                        "inventoryQuantity": 0,
                                        "selectedOptions": [{"name": "Size", "value": "10"}],
                                    },
                                ]
                            },
                        }
                    ],
                }
            }
        }
    if "query Variant" in query:
        vid = variables["id"]
        if not vid.endswith("9009"):
            raise LookupError(f"variant not found at Shopify: {vid}")
        return {
            "data": {
                "productVariant": {
                    "id": vid,
                    "sku": "ND14-9-BLK",
                    "title": "9 / Black",
                    "price": {"amount": "57.99", "currencyCode": "USD"},  # price changed!
                    "availableForSale": True,
                    "inventoryQuantity": 3,
                    "product": {"id": "gid://shopify/Product/111"},
                }
            }
        }
    if "draftOrderCreate" in query:
        return {
            "data": {
                "draftOrderCreate": {
                    "draftOrder": {
                        "id": "gid://shopify/DraftOrder/55",
                        "name": "#D55",
                        "invoiceUrl": "https://x",
                        "status": "OPEN",
                    },
                    "userErrors": [],
                }
            }
        }
    raise AssertionError(f"unexpected query in test transport: {query[:60]}")


@pytest.fixture()
def shopify_env(db_session, monkeypatch):
    """Seed a connected shopify merchant backed by a mocked HTTP transport."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        calls.append((str(request.url), body))
        try:
            payload = _shopify_payload(body["query"], body.get("variables") or {})
            return httpx.Response(200, json=payload)
        except LookupError as exc:
            return httpx.Response(
                200,
                json={"data": {"productVariant": None}, "errors": [{"message": str(exc)}]},
            )

    def adapter_post(url, json=None, headers=None, timeout=None):
        request = httpx.Request("POST", url, content=json_module_dumps(json))
        return handler(request)

    monkeypatch.setattr("app.adapters.commerce.shopify.httpx.post", adapter_post)
    merchant = upsert_shopify_merchant(db_session, "real-velocity.myshopify.com", "shpat_faketoken")
    db_session.commit()
    return db_session, merchant, calls


class TestShopifyAdapter:
    def test_metadata_bootstrap(self, shopify_env):
        _db, merchant, _calls = shopify_env
        assert merchant.name == "Real Velocity"
        assert merchant.slug == "real-velocity"

    def test_token_is_encrypted_at_rest(self, shopify_env):
        from sqlalchemy import select

        from app.db.models import MerchantIntegration

        db, merchant, _ = shopify_env
        row = db.scalar(select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant.id))
        assert row.auth_reference_encrypted is not None
        assert "shpat_faketoken" not in row.auth_reference_encrypted

        from app.core.security import decrypt_secret

        assert decrypt_secret(row.auth_reference_encrypted) == "shpat_faketoken"

    def test_sync_catalog_maps_gid_and_money(self, shopify_env):
        from sqlalchemy import select

        from app.db.models import Product, ProductVariant

        db, merchant, _ = shopify_env
        adapter = ShopifyAdapter()
        result = adapter.sync_catalog(db, merchant.id)

        assert result.products_synced == 1
        assert result.variants_synced == 2

        product = db.scalar(select(Product).where(Product.external_id == "gid://shopify/Product/111"))
        assert product.title == "Nike Downshifter 14"
        assert product.brand == "Nike"

        variant = db.scalar(
            select(ProductVariant).where(ProductVariant.external_id == "gid://shopify/ProductVariant/9009")
        )
        # "47.99" USD -> 4799 minor units
        assert variant.price == 4799
        assert variant.available_for_sale is True
        assert variant.options_json["size"] == "9"
        assert variant.options_json["color"] == "Black"

    def test_live_validation_detects_price_change(self, shopify_env):
        db, merchant, _ = shopify_env
        adapter = ShopifyAdapter()
        state = adapter.live_validate_variant(db, merchant.id, "gid://shopify/ProductVariant/9009")
        assert state.price_minor == 5799  # live price moved from 47.99 to 57.99
        assert state.available_for_sale is True

    def test_live_validation_missing_variant_raises(self, shopify_env):
        db, merchant, _ = shopify_env
        adapter = ShopifyAdapter()
        with pytest.raises(LookupError):
            adapter.live_validate_variant(db, merchant.id, "gid://shopify/ProductVariant/404")

    def test_draft_order_push(self, shopify_env):
        db, merchant, _ = shopify_env
        adapter = ShopifyAdapter()
        result = adapter.create_source_order(
            db,
            merchant.id,
            {
                "variant_gid": "gid://shopify/ProductVariant/9009",
                "quantity": 1,
                "txn_ref": "txn_test_1",
            },
        )
        assert result.reference == "gid://shopify/DraftOrder/55"


class TestOAuthHelpers:
    def test_normalize_host_accepts_forms(self):
        assert normalize_store_host("https://acme-store.myshopify.com/admin") == "acme-store.myshopify.com"
        assert normalize_store_host("acme.myshopify.com") == "acme.myshopify.com"

    def test_normalize_host_rejects_non_shopify(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            normalize_store_host("https://evil.example.com")

    def test_state_roundtrip_and_tamper(self):
        url, state = build_authorize_url("acme.myshopify.com")
        assert "acme.myshopify.com/admin/oauth/authorize" in url
        data = read_state(state)
        assert data is not None
        assert data["shop"] == "acme.myshopify.com"

        body, sig = state.rsplit(".", 1)
        tampered = f"{body[:-4]}AAAA.{sig}"
        assert read_state(tampered) is None

    def test_callback_hmac_verification(self):
        settings = get_settings()
        secret = settings.shopify_api_secret or settings.secret_key
        import hashlib
        import hmac as hmac_mod

        params = {"code": "abc123", "shop": "acme.myshopify.com", "state": "st"}
        message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        digest = hmac_mod.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

        assert verify_callback_hmac({**params, "hmac": digest}) is True
        assert verify_callback_hmac({**params, "hmac": "bad"}) is False
