"""Razorpay provider tests against a mocked REST transport."""

import hashlib
import hmac

import httpx
import pytest

from app.adapters.payments.razorpay import RazorpayProvider

KEY_ID = "rzp_test_keyid"
KEY_SECRET = "testsecret"


@pytest.fixture()
def provider(monkeypatch):
    sent: list[tuple[str, str, dict | None]] = []

    def fake_post(url, auth=None, json=None, timeout=None):
        sent.append((url, str(auth), json))
        if url.endswith("/orders"):
            return httpx.Response(
                200,
                json={
                    "id": "order_Rtzx1",
                    "amount": json["amount"],
                    "currency": json["currency"],
                    "receipt": json["receipt"],
                    "status": "created",
                },
            )
        if url.endswith("/capture"):
            return httpx.Response(200, json={"id": "pay_1", "status": "captured"})
        return httpx.Response(404, json={"error": {"description": "not found"}})

    def fake_get(url, auth=None, timeout=None):
        return httpx.Response(200, json={"id": url.rsplit("/", 1)[-1], "status": "authorized"})

    monkeypatch.setattr("app.adapters.payments.razorpay.httpx.post", fake_post)
    monkeypatch.setattr("app.adapters.payments.razorpay.httpx.get", fake_get)
    return RazorpayProvider(key_id=KEY_ID, key_secret=KEY_SECRET), sent


class TestRazorpayProvider:
    def test_create_order_sends_paise_amount(self, provider):
        p, sent = provider
        order = p.create_order(479900, "INR", receipt="txn_x", notes={"txn_ref": "txn_x"})
        assert order.provider_order_id == "order_Rtzx1"
        assert order.amount_minor == 479900
        url, auth, body = sent[0]
        assert url == f"{httpx_post_base()}/orders"
        assert body["amount"] == 479900  # integer minor units, never floats
        assert KEY_ID in auth and KEY_SECRET in auth

    def test_signature_verification_roundtrip(self, provider):
        p, _ = provider
        sig = hmac.new(KEY_SECRET.encode(), b"order_1|pay_1", hashlib.sha256).hexdigest()
        assert (
            p.verify_payment_signature(
                razorpay_order_id="order_1", razorpay_payment_id="pay_1", razorpay_signature=sig
            ).verified
            is True
        )
        assert (
            p.verify_payment_signature(
                razorpay_order_id="order_1",
                razorpay_payment_id="pay_1",
                razorpay_signature="deadbeef",
            ).verified
            is False
        )

    def test_capture_reports_status(self, provider):
        p, _ = provider
        result = p.capture_payment("pay_1", 479900, "INR")
        assert result.captured is True
        assert result.status == "captured"


def httpx_post_base() -> str:
    from app.adapters.payments.razorpay import BASE_URL

    return BASE_URL
