"""The six agent-facing tools (PRD §21).

Tools bind the capability contract to services. Note what is deliberately
absent: no Shopify access, no Razorpay access, no policy mutation, and no
free-form amounts anywhere.
"""

import secrets

from app.agent.base import Tool, ToolContext
from app.domain.contracts import (
    CheckoutRequest,
    CreateCartRequest,
    QuoteRequest,
    SearchFilters,
    SearchProductsRequest,
)
from app.domain.enums import Actor, EventType
from app.services import carts as cart_service
from app.services import catalog as catalog_service
from app.services import checkout as checkout_service
from app.services import quotes as quote_service
from app.services.audit import record_event
from app.services.profile_builder import build_profile
from app.services.transactions import TransactionService


def _discover(ctx: ToolContext, args: dict) -> dict:
    merchant = ctx.merchant
    integration = db_scalar_integration(ctx.db, merchant.id)
    profile = build_profile(ctx.db, merchant, integration=integration)

    # Attach to transaction pipeline + audit trail.
    txn_service = TransactionService(ctx.db)
    txn = txn_service.get_active_for_session(ctx.session.session_id) or txn_service.create(
        session_id=ctx.session.session_id, merchant_id=merchant.id
    )
    record_event(
        ctx.db,
        txn.id,
        EventType.MERCHANT_DISCOVERED,
        Actor.BUYER_AGENT,
        {"merchant": profile.merchant.name, "category": profile.merchant.category},
    )
    ctx.db.commit()
    return {
        "merchant_id": profile.merchant.id,
        "name": profile.merchant.name,
        "category": profile.merchant.category,
        "currency": profile.commerce.currency,
        "capabilities": [c.value for c in profile.commerce.capabilities],
        "policies": profile.policies.model_dump(),
        "txn_ref": txn.txn_ref,
    }


def db_scalar_integration(db, merchant_id):
    from sqlalchemy import select

    from app.db.models import MerchantIntegration

    return db.scalar(select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant_id))


def _search(ctx: ToolContext, args: dict) -> dict:
    req = SearchProductsRequest(
        query=args.get("query", ""),
        filters=SearchFilters(available_only=True),
        limit=int(args.get("limit", 10)),
    )
    results = catalog_service.search_products(ctx.db, ctx.merchant.id, req)
    compact = []
    for product in results:
        compact.append(
            {
                "product_id": product.id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category,
                "variants": [
                    {
                        "variant_id": v.id,
                        "external_variant_id": v.external_id,
                        "options": v.options,
                        "price_minor": v.price_minor,
                        "available": v.available_for_sale,
                    }
                    for v in product.variants
                ],
            }
        )
    return {"count": len(compact), "products": compact}


def _get_product(ctx: ToolContext, args: dict) -> dict:
    product, variants = catalog_service.get_product_or_404(ctx.db, ctx.merchant.id, args["product_id"])
    # Note: formal PRODUCT_SELECTED transition happens in the quote flow;
    # inspecting details alone does not commit a selection.
    return {
        "product_id": product.id,
        "title": product.title,
        "description": product.description,
        "brand": product.brand,
        "category": product.category,
        "variants": [
            {
                "variant_id": v.id,
                "external_variant_id": v.external_id,
                "sku": v.sku,
                "options": dict(v.options_json or {}),
                "price_minor": v.price,
                "available_for_sale": v.available_for_sale,
            }
            for v in variants
        ],
    }


def _get_quote(ctx: ToolContext, args: dict) -> dict:
    req = QuoteRequest(
        product_id=args["product_id"],
        variant_id=args["variant_id"],
        quantity=int(args.get("quantity", 1)),
    )
    quote_contract, _row = quote_service.create_quote(ctx.db, ctx.session, ctx.merchant, req)
    return quote_contract.model_dump()


def _create_cart(ctx: ToolContext, args: dict) -> dict:
    quote_row = quote_service.get_quote_row_by_ref(ctx.db, args["quote_id"])
    contract, row = cart_service.create_cart(
        ctx.db, ctx.session.session_id, quote_row, CreateCartRequest(quote_id=args["quote_id"])
    )

    txn_service = TransactionService(ctx.db)
    txn = txn_service.get_active_for_session(ctx.session.session_id)
    if txn is not None:
        record_event(
            ctx.db,
            txn.id,
            EventType.CART_CREATED,
            Actor.BUYER_AGENT,
            {"cart_ref": row.cart_ref, "total_minor": row.total_amount, "stage": "pre-checkout"},
        )
        ctx.db.commit()
    return contract.model_dump()


def _checkout(ctx: ToolContext, args: dict) -> dict:
    # Idempotency key is derived server-side; agents cannot influence it.
    key = f"{ctx.session.session_id}-{secrets.token_hex(6)}"
    req = CheckoutRequest(quote_id=args["quote_id"], idempotency_key=key)
    result = checkout_service.run_checkout(ctx.db, ctx.session, ctx.merchant, req)
    return result


def build_tools() -> list[Tool]:
    return [
        Tool(
            name="discover_merchant",
            description="Discover the connected merchant: identity, category, capabilities, policies.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            fn=_discover,
        ),
        Tool(
            name="search_products",
            description="Search the merchant catalog. Returns products with available variants and prices.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free text search terms"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": [],
                "additionalProperties": False,
            },
            fn=_search,
        ),
        Tool(
            name="get_product",
            description="Get authoritative details for one product including all variants.",
            parameters={
                "type": "object",
                "properties": {"product_id": {"type": "string"}},
                "required": ["product_id"],
                "additionalProperties": False,
            },
            fn=_get_product,
        ),
        Tool(
            name="get_quote",
            description=(
                "Get a live, authoritative quote for a specific variant. "
                "The quote snapshot is required before any purchase."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "variant_id": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["product_id", "variant_id"],
                "additionalProperties": False,
            },
            fn=_get_quote,
        ),
        Tool(
            name="create_cart",
            description="Create a cart/transaction context from a live quote.",
            parameters={
                "type": "object",
                "properties": {"quote_id": {"type": "string"}},
                "required": ["quote_id"],
                "additionalProperties": False,
            },
            fn=_create_cart,
        ),
        Tool(
            name="checkout",
            description=(
                "Attempt checkout for a quote. Passes through the deterministic policy engine; "
                "may be BLOCKED. Payment executes only when every rule passes."
            ),
            parameters={
                "type": "object",
                "properties": {"quote_id": {"type": "string"}},
                "required": ["quote_id"],
                "additionalProperties": False,
            },
            fn=_checkout,
        ),
    ]
