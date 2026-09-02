# AI Commerce Strategy Team

> An **always-on AI strategy team for merchants**. Enter a store URL — get a
> persistent fleet of specialist agents that researches your market, monitors
> competitors, simulates AI buyers, tracks your reputation, and turns evidence
> into prioritized, actionable strategy.

Razorpay AI Buildathon · Track 01 · pivoted from V0 (commerce gateway → strategy team, see `PRD_3.md`)

> **Start here:** [`docs/DEEP_DIVE.md`](docs/DEEP_DIVE.md) — the complete build
> document: every API, every design decision with its rationale, the full data
> model, ASCII architecture diagrams, and the chronological bug log (25 entries)
> explaining why non-obvious code exists.

## The one-line thesis

```
The baseline is Day 0. The persistent AI team is the product.
```

## What this is

1. A merchant onboards with a public store URL + an optional goal.
2. A **Day-0 baseline** runs as a mission graph: business research,
   competitor discovery, digital-presence scan and AI-buyer simulations —
   in parallel — then Strategy synthesizes everything into a versioned
   `BaselineSnapshot`.
3. The merchant launches **missions** ("Why is Competitor X winning beginners?").
   Missions are bounded units of work (budgets, timeouts, cancellation) and the
   future unit of billing.
4. Every claim lands as **Evidence → Findings → Recommendations** with full
   provenance: source URLs, observation timestamps, confidence, and explicit
   fact / inference / speculation tagging.
5. **Counterfactual experiments** run the same simulated buyer cohort against
   control vs variant messaging and report lift — always labeled `SIMULATED`,
   never presented as real revenue.
6. **Memory persists**: goals, competitor observations and experiment outcomes
   feed every future mission.

## The five specialists

| Agent | Mission |
|---|---|
| Market Intelligence | Category trends, new entrants, opportunities & threats |
| Competitor Intelligence | Pricing, positioning, product changes, review sentiment |
| AI Buyer Simulation | Realistic buyers complete purchase missions; report friction honestly |
| Digital Presence | How the public web actually perceives the merchant |
| Strategy | Synthesizes evidence into ranked recommendations + suggested next missions |

Agents share one runtime contract (`app/agents/base.py`), scoped tool
allowlists, bounded sub-agent fan-out (depth ≤ 1), and per-run budgets.

## Architecture

```
apps/api    FastAPI modular monolith
  engine/   mission lifecycle, dual-driver queue (Redis ⇄ in-process),
            executor w/ defensive state transitions, budgets, limits, SSE bus
  agents/   contract + runtime; five specialists in intel/agents_def.py
  tools/    Composio live plane (SEARCH/READ) + deterministic mock plane
  llm/      provider-agnostic; OpenAI-compatible impl w/ model fallback chain
  memory/   Mem0 adapter + local Postgres fallback behind one interface
  intel/    baseline graph, evidence/findings/recs persistence, experiments
  gateway/, transactions/, agent/, demo/   ← intact Legacy V0 commerce stack
apps/web    React + Vite command center (design language borrowed from
            aside.com: near-black surfaces, sky accent, eyebrow→headline→proof)
docker-compose.yml   postgres + redis + api (embedded worker) + web
docs/scaling.md      measured load-test results (PRD_3 §23.12)
```

Key invariants:

- PostgreSQL is durable truth; Redis is transient coordination only.
- Every external call has timeout + backoff-with-jitter retries + trace ids.
- Concurrent missions can't corrupt each other: conditional state transitions,
  atomic budget claims, idempotent creation endpoints.
- No mission is ever stuck RUNNING: wall-clock deadlines, cooperative cancel,
  lease expiry requeue.
- Simulated metrics are labeled SIMULATED end-to-end.

## Quickstart

```bash
cp .env.example .env        # add STRATEGY_LLM_* (OpenRouter works), COMPOSIO_API_KEY, MEM0_API_KEY
docker compose up --build   # postgres + redis + api + web
```

- Command center: http://localhost:5173
- API docs: http://localhost:8000/docs

Bare-metal dev also works without Docker/Redis (in-process queue driver +
SQLite):

```bash
cd apps/api && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --reload          # embedded worker starts automatically
cd ../web && npm install && npm run dev
```

Missing keys degrade gracefully: no LLM key → agents fail loudly but the
engine stays healthy; no Composio → deterministic mock tool plane; no Mem0 →
local memory adapter.

## Demo story (PRD_3 §32)

1. Onboard `https://some-store.in` (+ goal) → watch five agents work in parallel
2. Baseline snapshot v1 written; open findings, drill into evidence
3. Launch "Why is Competitor X outperforming us for beginners?"
4. Watch fan-out execution, evidence accumulating live over SSE
5. Strategy ranks recommendations; click "Launch it" on the suggested next mission
6. Create a control/variant experiment → simulated lift, clearly labeled

## Tests

```bash
cd apps/api && python -m pytest      # 99 tests: engine, agents, API, scenarios
```

Covers §34 scenarios: parallel missions, budget exhaustion, sub-agent bounds,
timeout/cancel paths, unknown-handler failure, duplicate-execution safety,
tool-failure degradation, strategy synthesis, experiment arms + labeling.

Load validation: see `docs/scaling.md`.

## Status

PRD_3 MVP complete through Phase 11 (demo hardening). Legacy V0 gateway
remains fully functional under the "V0 Commerce" nav section.
