"""Canonical domain enums shared across the platform."""

from enum import Enum


class Capability(str, Enum):
    """V0 capability contract (PRD §11)."""

    DISCOVER = "discover"
    SEARCH_PRODUCTS = "search_products"
    GET_PRODUCT = "get_product"
    GET_QUOTE = "get_quote"
    CREATE_CART = "create_cart"
    CHECKOUT = "checkout"


class MerchantStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class IntegrationProvider(str, Enum):
    SHOPIFY = "shopify"
    MOCK = "mock"


class IntegrationStatus(str, Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


class ProductStatus(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class CartStatus(str, Enum):
    OPEN = "open"
    CONVERTED = "converted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TransactionStatus(str, Enum):
    """Strict financial pipeline states (PRD §15)."""

    DISCOVERED = "DISCOVERED"
    PRODUCT_SELECTED = "PRODUCT_SELECTED"
    QUOTE_CREATED = "QUOTE_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    AUTHORIZED = "AUTHORIZED"
    BLOCKED = "BLOCKED"
    CART_CREATED = "CART_CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    COMPLETED = "COMPLETED"


class EventType(str, Enum):
    """Audit event types (PRD §20)."""

    USER_INTENT = "USER_INTENT"
    MERCHANT_DISCOVERED = "MERCHANT_DISCOVERED"
    PRODUCT_SEARCHED = "PRODUCT_SEARCHED"
    PRODUCT_SELECTED = "PRODUCT_SELECTED"
    QUOTE_CREATED = "QUOTE_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    CART_CREATED = "CART_CREATED"
    PAYMENT_ORDER_CREATED = "PAYMENT_ORDER_CREATED"
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    TRANSACTION_BLOCKED = "TRANSACTION_BLOCKED"
    TRANSACTION_COMPLETED = "TRANSACTION_COMPLETED"


class Actor(str, Enum):
    BUYER_AGENT = "buyer_agent"
    GATEWAY = "gateway"
    POLICY_ENGINE = "policy_engine"
    MERCHANT = "merchant"
    PAYMENT_PROVIDER = "payment_provider"
    SYSTEM = "system"
