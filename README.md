# Agent Commerce Gateway

> Turn a normal Shopify merchant into an **AI-native merchant** — AI discoverable, AI interactable,
> AI transactable — without rebuilding the human-facing storefront.

Razorpay AI Buildathon · Track 01: AI Growth & Agentic Commerce · Shopify V0

## What this is

A Merchant Agent Layer + Agent Commerce Gateway:

1. A Shopify merchant connects once (OAuth).
2. The platform syncs catalog data into a canonical commerce model and generates a **Merchant Agent Profile**.
3. Buyer agents interact through standardized capabilities: `discover`, `search_products`, `get_product`, `get_quote`, `create_cart`, `checkout`.
4. Every financial action passes a **deterministic policy engine** — the LLM can propose, it can never authorize money.
5. Authorized payments execute through **Razorpay (Test Mode)** behind a `PaymentProvider` interface.
6. Every decision lands in an auditable transaction timeline.

## Repository layout

```
apps/
  api/     FastAPI modular monolith (gateway, policy engine, adapters, buyer agent)
  web/     React + Vite merchant dashboard (Terminal Fintech UI)
docs/      architecture notes
docker-compose.yml
```

## Quickstart (local)

```bash
cp .env.example .env          # fill in Shopify/Razorpay creds when you have them
docker compose up db -d       # Postgres on localhost:5439

cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload            # http://localhost:8000  (/docs)

cd ../web
npm install
npm run dev                              # http://localhost:5173
```

Or everything at once: `docker compose up --build`.

## Setup guides

- Shopify Partner org + dev store + OAuth app: see `docs/setup-shopify.md`
- Razorpay Test Mode keys: see `docs/setup-razorpay.md`

## Status

V0 under active construction — see PRD.md for the full specification.
