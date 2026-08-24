# Razorpay Test Mode Guide

Razorpay executes authorized payments. Test Mode uses fake money end-to-end.

## 1. Get test keys

1. Log in to the [Razorpay Dashboard](https://dashboard.razorpay.com).
2. Toggle **Test Mode** (top-right).
3. **Account & Settings → API Keys → Generate Test Key**.
4. Copy the Key Id and Key Secret into `.env`:

   ```env
   RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
   RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
   ```

5. Restart the API. `GET /healthz` now reports `"payment_provider": "razorpay"`.

Without keys the platform falls back to a deterministic mock provider that mirrors the
same interface - useful for offline demos.

## 2. How a payment completes

1. Buyer-agent checkout passes the policy engine → gateway creates a Razorpay **Order**
   for the validated quote amount (paise).
2. The dashboard opens Razorpay Checkout with that order id.
3. Pay with any test card, e.g.:

   ```
   Card     4111 1111 1111 1111
   Expiry   any future date
   CVV      any 3 digits
   ```

4. Checkout returns `order_id | payment_id | signature`; the client posts them to
   `POST /api/v1/transactions/{txn}/payment/complete`.
5. The server verifies the HMAC-SHA256 signature with the key secret, captures, marks the
   transaction COMPLETED, and pushes a draft order back to Shopify.

Amounts always originate from the stored quote row - never from model output.

## 3. Failure paths worth showing

- Wrong/missing signature → `PAYMENT_FAILED` with `signature_mismatch` in the audit trail.
- Block the transaction via a demo scenario first → no Razorpay order is ever created.

## Notes

- Webhooks are not required for V0; verification happens synchronously on the completion
  endpoint.
- Keep test keys out of git; `.env` is ignored.
