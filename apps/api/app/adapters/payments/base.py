"""Payment provider abstraction (PRD §18).

Razorpay sits behind this interface; the checkout orchestrator only ever
talks to `PaymentProvider`, and amounts always originate from validated
transaction state - never from LLM output.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class PaymentOrder:
    provider_order_id: str
    amount_minor: int
    currency: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaymentVerification:
    verified: bool
    payment_id: str | None = None
    reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CaptureResult:
    captured: bool
    payment_id: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, str] | None = None,
    ) -> PaymentOrder: ...

    def verify_payment_signature(
        self,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> PaymentVerification: ...

    def fetch_payment(self, payment_id: str) -> dict[str, Any]: ...

    def capture_payment(self, payment_id: str, amount_minor: int, currency: str) -> CaptureResult: ...
