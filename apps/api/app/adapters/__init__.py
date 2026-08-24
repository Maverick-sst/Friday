"""Adapter registries.

The active commerce adapter is chosen per merchant from its integration row;
the payment provider is selected from configured credentials (Razorpay when
keys exist, otherwise the deterministic MockPaymentProvider).
"""

from functools import lru_cache

from app.adapters.commerce.base import CommerceAdapter
from app.adapters.commerce.mock import MockAdapter
from app.adapters.payments.base import PaymentProvider
from app.adapters.payments.mock_provider import MockPaymentProvider
from app.core.config import get_settings


def get_commerce_adapter(provider: str) -> CommerceAdapter:
    if provider == "shopify":
        # Imported lazily to avoid a hard dependency before Phase 4 lands.
        from app.adapters.commerce.shopify import ShopifyAdapter

        return ShopifyAdapter()
    if provider == "mock":
        return MockAdapter()
    raise ValueError(f"Unknown commerce provider: {provider}")


@lru_cache
def _mock_payment_singleton() -> MockPaymentProvider:
    return MockPaymentProvider()


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.razorpay_key_id and settings.razorpay_key_secret:
        from app.adapters.payments.razorpay import RazorpayProvider

        return RazorpayProvider(
            key_id=settings.razorpay_key_id, key_secret=settings.razorpay_key_secret
        )
    return _mock_payment_singleton()


def payment_provider_name() -> str:
    settings = get_settings()
    if settings.razorpay_key_id and settings.razorpay_key_secret:
        return "razorpay"
    return "mock"
