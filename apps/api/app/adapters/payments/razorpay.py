"""Razorpay Test Mode provider (PRD §18).

Thin adapter over Razorpay's REST API using Basic auth. Amounts are always
integer minor units (paise). Signature verification follows Razorpay's
order|payment|secret HMAC-SHA256 scheme.
"""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.adapters.payments.base import (
    CaptureResult,
    PaymentOrder,
    PaymentVerification,
)

logger = logging.getLogger("acg.razorpay")

BASE_URL = "https://api.razorpay.com/v1"


class RazorpayProvider:
    name = "razorpay"

    def __init__(self, key_id: str, key_secret: str):
        self._auth = (key_id, key_secret)

    # -- PaymentProvider surface ---------------------------------------------------

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str] | None = None,
    ) -> PaymentOrder:
        payload = httpx.post(
            f"{BASE_URL}/orders",
            auth=self._auth,
            json={
                "amount": amount_minor,
                "currency": currency,
                "receipt": receipt,
                "notes": notes or {},
            },
            timeout=25,
        )
        data = _json_or_error(payload)
        logger.info(
            "razorpay order created",
            extra={"extra_fields": {"order_id": data.get("id"), "amount": amount_minor}},
        )
        return PaymentOrder(
            provider_order_id=data["id"],
            amount_minor=data.get("amount", amount_minor),
            currency=data.get("currency", currency),
            raw=data,
        )

    def verify_payment_signature(
        self,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> PaymentVerification:
        expected = hmac.new(
            self._auth[1].encode(),
            f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        verified = hmac.compare_digest(expected, razorpay_signature)
        return PaymentVerification(
            verified=verified,
            payment_id=razorpay_payment_id,
            reason=None if verified else "signature_mismatch",
        )

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        response = httpx.get(f"{BASE_URL}/payments/{payment_id}", auth=self._auth, timeout=25)
        return _json_or_error(response)

    def capture_payment(self, payment_id: str, amount_minor: int, currency: str) -> CaptureResult:
        response = httpx.post(
            f"{BASE_URL}/payments/{payment_id}/capture",
            auth=self._auth,
            json={"amount": amount_minor, "currency": currency},
            timeout=25,
        )
        data = _json_or_error(response)
        status_value = data.get("status")
        captured = status_value in ("captured",)
        return CaptureResult(captured=captured, payment_id=payment_id, status=status_value, raw=data)


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"Razorpay HTTP {response.status_code}: {response.text[:300]}")
    data: dict[str, Any] = response.json()
    if "error" in data and data.get("error"):
        raise RuntimeError(f"Razorpay error: {data['error']}")
    return data
