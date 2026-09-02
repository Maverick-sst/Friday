"""Mock payment provider: deterministic, zero-dependency test rail.

Used until Razorpay Test Mode credentials are configured. Mirrors the exact
interface of the real provider so swapping is a config change only.
"""

from typing import Any

from app.adapters.payments.base import (
    CaptureResult,
    PaymentOrder,
    PaymentVerification,
)


class MockPaymentProvider:
    name = "mock"

    def __init__(self) -> None:
        self._counter = 0
        # Simulated in-memory payment states keyed by order id.
        self._payments: dict[str, dict[str, Any]] = {}

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str] | None = None,
    ) -> PaymentOrder:
        self._counter += 1
        order_id = f"mock_order_{self._counter:08d}"
        self._payments[order_id] = {
            "amount_minor": amount_minor,
            "currency": currency,
            "receipt": receipt,
            "status": "created",
            "payment_id": f"mock_pay_{self._counter:08d}",
        }
        return PaymentOrder(
            provider_order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            raw={"receipt": receipt, "provider": "mock"},
        )

    def verify_payment_signature(
        self,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> PaymentVerification:
        record = self._payments.get(razorpay_order_id)
        if record is None or record["payment_id"] != razorpay_payment_id:
            # The mock stands in for the hosted Checkout: any payment id
            # presented for a known order is treated as a sandbox success.
            if record is None:
                return PaymentVerification(verified=False, reason="unknown mock order/payment pair")
        if record["status"] == "failed":
            return PaymentVerification(
                verified=False,
                payment_id=razorpay_payment_id,
                reason="mock failure injected",
            )
        record.setdefault("payment_id", razorpay_payment_id)
        record["status"] = "authorized"
        return PaymentVerification(verified=True, payment_id=razorpay_payment_id)

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        for record in self._payments.values():
            if record["payment_id"] == payment_id:
                return {"id": payment_id, "status": record["status"]}
        raise LookupError(f"unknown mock payment {payment_id}")

    def capture_payment(self, payment_id: str, amount_minor: int, currency: str) -> CaptureResult:
        for record in self._payments.values():
            if record["payment_id"] == payment_id and record["status"] == "authorized":
                if record["amount_minor"] != amount_minor:
                    return CaptureResult(
                        captured=False, payment_id=payment_id, status="capture_amount_mismatch"
                    )
                record["status"] = "captured"
                return CaptureResult(captured=True, payment_id=payment_id, status="captured")
        return CaptureResult(captured=False, payment_id=payment_id, status="not_capturable")

    # -- test/demo helpers -------------------------------------------------------

    def inject_failure(self, order_id: str) -> None:
        """Force the next verification for this order to fail (demo hook)."""
        if order_id in self._payments:
            self._payments[order_id]["status"] = "failed"
