"""Typed contracts shared by API, adapters, policy engine, and frontend.

These Pydantic models are the canonical language of the platform (PRD §9.5).
Amounts are integer minor units; every money-bearing contract carries its
currency explicitly.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import Capability

# --------------------------------------------------------------------------
# Merchant Agent Profile (PRD §10)
# --------------------------------------------------------------------------


class MerchantIdentity(BaseModel):
    id: str
    name: str
    description: str | None = None
    category: str | None = None
    subcategories: list[str] = Field(default_factory=list)
    website: str | None = None
    logo_url: str | None = None
    agent_endpoint: str | None = None


class MerchantCommerce(BaseModel):
    currency: str = "INR"
    capabilities: list[Capability] = Field(default_factory=list)


class MerchantPolicyView(BaseModel):
    """Merchant policies as exposed in the canonical profile."""

    max_auto_purchase_minor: int = Field(ge=0)
    approval_threshold_minor: int = Field(ge=0)
    allowed_categories: list[str] = Field(default_factory=list)
    currency: str = "INR"
    return_window_days: int = Field(default=7, ge=0)
    allow_cancellation: bool = True


class MerchantSourceInfo(BaseModel):
    provider: str
    store_url: str | None = None
    last_synced_at: datetime | None = None


class MerchantAgentProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    profile_version: int = 1
    merchant: MerchantIdentity
    commerce: MerchantCommerce
    policies: MerchantPolicyView
    source: MerchantSourceInfo


# --------------------------------------------------------------------------
# Buyer authorization (PRD §13)
# --------------------------------------------------------------------------


class BuyerAuthorization(BaseModel):
    buyer_id: str = "demo-user"
    max_amount_minor: int = Field(gt=0)
    currency: str = "INR"
    allowed_categories: list[str] | None = None  # None => any category the merchant allows
    intent: str = ""
    issued_at: datetime
    expires_at: datetime | None = None


# --------------------------------------------------------------------------
# Catalog (PRD §11.2 / §11.3)
# --------------------------------------------------------------------------


class ProductVariant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sku: str | None = None
    title: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    price_minor: int = Field(ge=0)
    currency: str = "INR"
    available_quantity: int | None = None
    available_for_sale: bool = False


class Product(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str | None = None
    title: str
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    status: str = "active"
    variants: list[ProductVariant] = Field(default_factory=list)


class SearchFilters(BaseModel):
    category: str | None = None
    brand: str | None = None
    max_price_minor: int | None = Field(default=None, ge=0)
    available_only: bool = True

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Quotes & carts (PRD §11.4 / §11.5)
# --------------------------------------------------------------------------


class QuoteLine(BaseModel):
    product_id: str
    variant_id: str
    quantity: int = Field(default=1, ge=1)
    unit_price_minor: int = Field(ge=0)


class Quote(BaseModel):
    quote_id: str
    merchant_id: str
    session_id: str | None = None
    lines: list[QuoteLine] = Field(default_factory=list)

    subtotal_minor: int = Field(ge=0)
    shipping_minor: int = Field(default=0, ge=0)
    tax_minor: int = Field(default=0, ge=0)
    total_minor: int = Field(ge=0)
    currency: str = "INR"

    inventory_available: bool = True
    expires_at: datetime
    source: str = "shopify"
    live_validated: bool = True


class CartItem(BaseModel):
    product_id: str
    variant_id: str
    quantity: int = Field(default=1, ge=1)
    unit_price_minor: int = Field(ge=0)
    total_minor: int = Field(ge=0)


class Cart(BaseModel):
    cart_id: str
    merchant_id: str
    session_id: str | None = None
    quote_id: str | None = None
    items: list[CartItem] = Field(default_factory=list)
    total_minor: int = Field(ge=0)
    currency: str = "INR"
    status: str = "open"


# --------------------------------------------------------------------------
# Policy decisions (PRD §14)
# --------------------------------------------------------------------------


class PolicyCheck(BaseModel):
    code: str
    passed: bool
    detail: str = ""


class PolicyDecision(BaseModel):
    allowed: bool
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str = ""
    checks: list[PolicyCheck] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Gateway request payloads (agent-facing)
# --------------------------------------------------------------------------


class SearchProductsRequest(BaseModel):
    query: str = ""
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=10, ge=1, le=50)


class QuoteRequest(BaseModel):
    product_id: str
    variant_id: str
    quantity: int = Field(default=1, ge=1, le=10)
    shipping_destination: dict[str, Any] | None = None


class CreateCartRequest(BaseModel):
    quote_id: str
    items: list[QuoteLine] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def non_negative(cls, v: list[QuoteLine]) -> list[QuoteLine]:
        if len(v) > 10:
            raise ValueError("at most 10 line items per cart in V0")
        return v


class CheckoutRequest(BaseModel):
    cart_id: str | None = None
    quote_id: str
    idempotency_key: str = Field(min_length=8, max_length=128)


# --------------------------------------------------------------------------
# Buyer agent session bootstrap
# --------------------------------------------------------------------------


class StartAgentSessionRequest(BaseModel):
    intent: str = Field(min_length=3, max_length=2000)
    max_budget_minor: int = Field(gt=0)
    currency: str = "INR"
    preferred_size: str | None = None
    preferred_category: str | None = None
