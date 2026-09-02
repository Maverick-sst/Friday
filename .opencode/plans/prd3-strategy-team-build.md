# Build Plan — AI Commerce Strategy Team (PRD_3 pivot)

## Context

**Pivot arc:** V0 (`PRD.md`) = Agent Commerce Gateway (Shopify → AI-transactable store, policy engine,
Razorpay test payments; 72 tests green). `PRD_2.md` = UCP/ACP alignment amendment (never built).
**`PRD_3.md` (current)** = full product pivot to an **always-on AI strategy team for merchants**:
onboard via public store URL → baseline diagnostic mission graph → persistent five-agent fleet
(Market / Competitor / AI Buyer Simulation / Digital Presence / Strategy) running bounded missions
that produce **evidence → findings → recommendations → simulated experiments**, with Mem0 memory,
Composio tool plane, Redis-backed concurrency, PostgreSQL as durable truth.

## Locked decisions (user-confirmed)

1. **Providers:** OpenAI-compatible LLM live from day 1. Composio + Mem0 integrated behind internal
   interfaces now, with deterministic fallback drivers until keys are attached later
   (PRD §37 Phase 11 mandates safe fallbacks regardless).
2. **Old V0 UI:** demoted to a collapsed "Legacy V0" nav section; backend commerce modules untouched.
3. **Redis:** first-class service in docker-compose + in-process asyncio fallback driver behind one
   `JobQueue` interface so tests/dev-without-docker still work.
4. **Workflow:** build autonomously with 3 checkpoint reviews.
5. **Sub-agent depth conflict (§11.2=1 vs §23.5=2):** config-driven, default `max_sub_agent_depth=1`
   (children never spawn children).

## Reuse audit (Phase 0 — complete)

- Keep as-is: FastAPI app factory/middleware/CORS, `core/` (config, errors, idempotency, security,
  logging), sync DB session + Alembic setup, Terminal Fintech design system (`apps/web`),
  entire V0 commerce stack (`gateway/`, `transactions/`, `adapters/`, `agent/`, `demo/`, seeds)
  preserved as legacy modules behind a feature flag for routes if needed.
- Repo-root `web/` is a stray create-vite scaffold (tracked in git) → propose deleting it.
- Tests run via `python -m pytest` from `apps/api` (venv at `apps/api/.venv`); baseline: 72 passed.

## Architecture shape

```
apps/api/app/
  engine/        # NEW: mission lifecycle states, queue, executor/graph runner, budgets,
                 #      limits, scheduler stubs, worker entrypoint, SSE progress bus
  agents/        # NEW: base.py contract+runtime; market.py, competitor.py, buyer.py,
                 #      presence.py, strategy.py
  tools/         # NEW: router.py (SEARCH/READ/ACT capability classes),
                 #      composio_plane.py, mock_plane.py (seeded deterministic corpus)
  llm/           # NEW: provider.py (LLMProvider iface), openai_compat.py
                 #      (JSON-mode structured_generate + validation/retry)
  memory/        # NEW: interface.py (MemoryStore), mem0_adapter.py, local_adapter.py
                 #      (Postgres-backed fallback)
  intel/         # NEW: baseline.py (mission graph), evidence.py, findings.py,
                 #      recommendations.py, experiments.py, usage.py
  db/models.py   # EXTEND (single models file per existing convention): merchant_profiles,
                 #      merchant_sources, agents, missions, agent_runs, evidence, findings,
                 #      recommendations, experiments, experiment_runs, baseline_snapshots,
                 #      usage_events, memory_refs
  db/session.py  # ADD async engine + async_sessionmaker alongside sync V0 sessions
```

Key invariants (PRD §23):
- Postgres = durable truth; Redis = transient coordination only.
- Every external call wrapped: timeout, retry w/ exponential backoff+jitter, trace id,
  structured error; tool failure degrades gracefully preserving partial evidence.
- Bounded concurrency everywhere: global/per-merchant mission limits, semaphores for fan-out,
  LLM concurrency cap; config-driven limits, never hard-coded.
- Idempotency keys on mission/experiment creation (reuse `core/idempotency.py`).
- No mission stuck in RUNNING: run-level + mission-level deadlines, cancellation checks between
  steps/tool calls, lease expiry requeues crashed worker jobs.
- Budget exhaustion ⇒ stop spawning, summarize partial results, mark `PARTIALLY_COMPLETED`.
- Every simulated metric labeled `SIMULATED` end-to-end.

## Milestones

| # | Work | Exit criteria |
|---|------|---------------|
| M1 | Models + migration 0003, config expansion, JobQueue (Redis + in-process), async sessions, worker entrypoint | Migration applies; worker drains stub mission; tests green |
| M2 | AgentContract + runtime, budget/timeout/cancel enforcement, SSE progress stream, REST surface (onboard / missions / agents / experiments) | Mission executes e2e via API with mock plane |
| M3 | Tool planes (Composio + seeded mock corpus), LLM provider, memory adapters | All external seams swappable via .env |
| M4 | Five specialist agents, baseline mission graph, versioned snapshots | Baseline runs URL → snapshot v1 |
| M5 | Findings/recs traceability, Strategy synthesis, experiment engine | Full §32 demo story headless |
| M6 | Frontend command center (Onboarding/Baseline/AI Team/Missions/Mission/Strategy/Experiments) + Legacy demotion | Demo clickable |
| M7 | Test matrix (§34 scenarios 1–9), load harness (progressive levels → measured ceiling in docs/scaling.md), README/compose updates | DoD walkthrough |

## New dependencies

`aiosqlite` (dev/tests), `redis` (queue driver). Composio/Mem0 accessed via thin httpx clients
(no heavy SDKs) unless SDK proves necessary.

## Checkpoints

- CP1 after M1–M2: engine foundation + runtime working headless.
- CP2 after M3–M5: full backend demo story (baseline → mission → findings → recs → experiment).
- CP3 after M6–M7: UI + hardening complete; measured scale numbers recorded.
