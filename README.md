# Agent Commerce Gateway

> Turn a normal Shopify merchant into an **AI-native merchant** — AI discoverable, AI interactable,
> AI transactable — without rebuilding the human-facing storefront.

Razorpay AI Buildathon · Track 01: AI Growth & Agentic Commerce · Shopify V0

## The one-line thesis

```
The LLM can decide what it wants to attempt.
It cannot unilaterally decide that money is allowed to move.
```

## What this is

A Merchant Agent Layer + Agent Commerce Gateway:

1. A Shopify merchant connects once (full OAuth). No website rebuild, no new catalog.
2. Catalog data is normalized into a canonical commerce model and a **Merchant Agent Profile**.
3. Buyer agents interact only through standardized capabilities:
   `discover → search_products → get_product → get_quote → create_cart → checkout`.
4. Quotes are **live snapshots**: price/inventory are revalidated at the source platform
   before any payment decision (stale-data protection).
5. Every checkout passes a **deterministic 12-rule policy engine** — effective spend cap is
   `min(merchant limit, buyer authorization)`.
6. Authorized payments execute through **Razorpay Test Mode** behind a `PaymentProvider` interface;
   completed purchases push back to Shopify as draft orders.
7. Every step lands in an auditable transaction timeline. Blocked transactions explain themselves.

## Demo story

| Scene | Where |
| --- | --- |
| Connect store / demo seed | **Connect Store** page |
| AI-native profile appears | **AI-Native Profile** page |
| Configure guardrails | **Policies** page |
| "Find Nike Downshifter 14 size 9 under ₹5,000…" | **Agent Console** → Run Buyer Agent |
| Policy passes → Razorpay test checkout → audit trail | console + **Transaction Trace** |
| Price flips ₹4,799 → ₹5,799 after quote → BLOCKED, no payment call | Console with scenario *price change after quote* |

## Architecture

```
apps/
  api/     FastAPI modular monolith
           ├── domain/      contracts, strict transaction state machine, reason codes
           ├── db/          SQLAlchemy models + Alembic migrations (PRD §19)
           ├── adapters/    commerce: MockAdapter | ShopifyAdapter (Admin GraphQL 2026-07)
           │                payments: RazorpayProvider | deterministic mock
           ├── services/    policy engine (pure), quotes, carts, checkout orchestrator, audit
           ├── gateway/     the six agent-facing capabilities
           ├── onboarding/  Shopify OAuth connect/callback/sync
           ├── agent/       tool-calling buyer agent: scripted brain (zero keys)
           │                or OpenAI-compatible LLM brain; SSE streaming
           └── demo/        deterministic failure-injection scenarios
  web/     React + Vite dashboard (Terminal Fintech design system)
docs/      setup-shopify.md · setup-razorpay.md
```

Key invariants (enforced server-side):

- `Transaction.status` changes only via the state machine; `BLOCKED`/`PAYMENT_*` are terminal for V0.
- Payment orders are created only from stored, live-validated quote amounts.
- Checkout is idempotent — retries replay the first response, never double-pay.
- Secrets (Shopify token, Razorpay keys) never leave the server.

## Quickstart

```bash
cp .env.example .env                      # fill creds when you have them
docker compose up --build                 # postgres + api + web
```

Or by hand:

```bash
docker compose up db -d                   # Postgres on localhost:5439
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload             # http://localhost:8000/docs
cd ../web && npm install && npm run dev   # http://localhost:5173
```

No Docker yet? The API also runs on SQLite for development:

```bash
DATABASE_URL="sqlite:///./dev.db" uvicorn app.main:app --reload
```

In development mode the API auto-seeds the mock merchant **Velocity Sports**
(Nike Downshifter 14 size 9 @ ₹4,799 etc.), so the full demo works before any
Shopify/Razorpay credentials exist.

## Setup guides

- Shopify Partner org + dev store + OAuth app: `docs/setup-shopify.md`
- Razorpay Test Mode keys + test card flow: `docs/setup-razorpay.md`

## Tests

```bash
cd apps/api && pytest            # 72 tests: unit + policy matrix + E2E scenarios
```

Scenario coverage mirrors PRD §31.3: valid purchases, price-mismatch blocks,
inventory failures, policy violations — with the global property
**0 unauthorized payment attempts**. Live numbers: `GET /api/v1/metrics`.

## Status

V0 complete per PRD §35 Definition of Done. See `PRD.md` for the full specification.
