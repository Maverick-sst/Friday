# DEEP DIVE — AI Commerce Strategy Team

> The complete build document: what exists, why it exists, how it works,
> every API, and every bug we hit along the way with its root cause.
> Read this top-to-bottom once and you have full context on the codebase.

---

## Table of Contents

1. [The Pivot Story](#1-the-pivot-story)
2. [System Architecture](#2-system-architecture)
3. [Repository Map](#3-repository-map)
4. [Data Model](#4-data-model)
5. [Mission Engine](#5-mission-engine)
6. [Agent Runtime](#6-agent-runtime)
7. [Tool Plane](#7-tool-plane)
8. [LLM Layer](#8-llm-layer)
9. [Memory Layer](#9-memory-layer)
10. [Baseline Graph](#10-baseline-graph)
11. [Experiment Engine](#11-experiment-engine)
12. [Complete API Reference](#12-complete-api-reference)
13. [Frontend](#13-frontend)
14. [Bug Log — Every Problem We Hit](#14-bug-log--every-problem-we-hit)
15. [Testing Strategy](#15-testing-strategy)
16. [Operations Runbook](#16-operations-runbook)
17. [Known Gaps & Honest Caveats](#17-known-gaps--honest-caveats)

---

## 1. The Pivot Story

The repo contains three PRDs because the product changed direction twice.
Understanding this explains why "legacy" code exists alongside the new engine:

```
PRD.md  (V0, BUILT & WORKING)
  "Agent Commerce Gateway": Shopify merchant -> AI-transactable store.
  Buyer agents discover products, get quotes, checkout through a
  deterministic 12-rule policy engine, pay via Razorpay test mode.
  Status: fully built, 72 tests, Terminal-Fintech dashboard.

        |  pivot #1 (PRD_2.md, never built)
        v  "Don't reinvent protocols; align with UCP/ACP instead."

PRD_3.md  (CURRENT, THIS BUILD)
  Complete product pivot: stop being commerce infrastructure entirely.
  Become an ALWAYS-ON AI STRATEGY TEAM for merchants:
     URL in -> baseline diagnostic -> persistent 5-agent fleet
     -> missions -> evidence -> findings -> recommendations
     -> simulated experiments -> memory -> next mission.
```

**Decision made:** preserve ALL working V0 code (it still runs, reachable
under the "V0 Commerce" nav section), build the new engine alongside it, share
infrastructure (config pattern, DB conventions, idempotency helpers) but never
entangle them. The policy-engine-as-experiment-gate idea from PRD_3 §24 is
deferred until real action integrations exist.

---

## 2. System Architecture

```
                          +---------------------------+
   Merchant (browser) --> |  apps/web  React+Vite      |
                          |  command center UI         |
                          +------------|--------------+
                                       | /api/* (vite proxy)
                                       v
+---------------------------------------------------------------+
|                    apps/api  FastAPI                           |
|                                                                |
|  REST surface (engine/routes.py)                               |
|    onboard . missions . cancel . intel . experiments . meta     |
|         |                                    ^                 |
|         v                                    | SSE events      |
|  +------------------+          +------------------------+     |
|  | JobQueue (iface) |          | ProgressBus            |     |
|  |  RedisJobQueue   |          |  local pubsub + redis  |     |
|  |  InProcessQueue  |          +------------------------+     |
|  +--------|---------+                                          |
|           | claim/release                                     |
|  +--------v---------+     +--------------------------------+   |
|  | Worker (embedded |     | Executor                       |   |
|  | or replicas)      |---->| defensive state transitions    |   |
|  +------------------+     | timeouts . cancel . budgets     |   |
|                            +-------|------------------------+   |
|                                    | handler registry          |
|                            +-------v------------------------+   |
|                            | intel/handlers.py              |   |
|                            |  baseline graph                |   |
|                            |  on_demand dispatch            |   |
|                            |  experiment arms               |   |
|                            +---|--------|---------|---------+   |
|                                |        |         |             |
|                     execute_agent_run(...) per agent run       |
|                                |                                |
|        +-----------------------+----------------+               |
|        v                       v                v               |
|  +-----------+   +---------------------+   +-----------+       |
|  | ToolPlane |   | LLMProvider         |   | MemoryStore|      |
|  | composio  |   | openai-compatible   |   | mem0 /     |       |
|  | mock      |   | (fallback chain)    |   | local pg   |       |
|  +-----------+   +---------------------+   +-----------+       |
|                                                                |
|  PostgreSQL = durable truth (missions, runs, evidence, ...)     |
+---------------------------------------------------------------+
        |                  |                  |
        v                  v                  v
   Postgres :5439      Redis :6380      Composio/OpenRouter/Mem0 APIs
```

Design principles enforced everywhere (PRD_3 §23):

- **Postgres is truth; Redis is coordination.** A Redis crash never loses data;
  jobs can always be replayed from QUEUED missions in the DB.
- **Every external call** has a timeout, bounded retries with
  exponential-backoff-plus-jitter, and returns structured errors — tool
  failures degrade a mission gracefully instead of killing it.
- **Bounded concurrency** at every level: global/per-merchant mission caps,
  worker fan-out limit, LLM semaphore, sub-agent depth ≤ 1.
- **No process-global mutable correctness state.** The only singletons are
  connection pools/drivers; all mutable state flows through function args or
  DB rows.

---

## 3. Repository Map

Annotated tree — *why* each directory exists:

```
apps/api/app/
  main.py               App factory + lifespan. Starts embedded worker(s)
                        under a crash-restarting supervisor when
                        EMBEDDED_WORKER=true (single-command demo). Legacy V0
                        routers mount behind enable_legacy_routes flag.

  core/                 V0 infra reused verbatim:
    config.py           pydantic-settings; EVERY knob lives here incl. engine
                        limits (never hard-code limits at call sites).
    idempotency.py      sync guard reused conceptually for mission/experiment
                        creation (async variant inline in routes.py).
    errors.py           GatewayError -> {code,message} problem details.

  db/
    models.py           ONE SQLAlchemy models file (V0 convention). V0 tables
                        untouched; strategy-team tables appended below them.
    session.py          Sync engine (legacy) + async engine/sessionmaker for
                        the new engine. Same declarative models work with both.
    seeds.py, demo_overrides.py   V0 demo merchant + failure-injection.

  migrations/versions/  0001..0002 = V0 schema. 0003 = additive-only strategy
                        team tables (see bug log #1 for what almost went wrong).

  engine/               THE MISSION ENGINE (product-agnostic machinery):
    state.py            status constants + terminal-state sets (§26 lifecycle).
    queue.py            JobQueue interface + Redis driver (Lua atomic claim,
                        leases, heartbeat, crash recovery) + InProcess driver
                        (single shared instance! see bug #7).
    limits.py           ConcurrencyLimiter: global/per-merchant mission caps +
                        LLM semaphore. Redis INCR-backed or asyncio-locked.
    context.py          MissionContext (per-execution), RunBudget (tool-call
                        counter), MissionCancelled vs BudgetExhausted, the
                        handler registry.
    executor.py         execute_mission(): claim->admit->RUNNING->handler->
                        terminal state. All transitions are conditional UPDATEs.
    progress.py         ProgressBus: transient event fan-out to SSE clients.
    routes.py           The entire /api/v1/team REST surface.
    service.py          create/enqueue/list/cancel mission functions.
    worker.py           `python -m app.engine.worker`: claims jobs, spawns
                        bounded tasks, heartbeats leases. Distributed-safe.

  agents/base.py        AgentContract + AgentRunResult pydantic schemas +
                        BaseSpecialistAgent (schema validation w/ one repair
                        retry). The ONLY thing agents must implement.

  intel/                PRODUCT LOGIC (what makes this our product):
    prompts.py          Each specialist's role/rules as prompt text.
    schemas.py          Per-output-type pydantic models the LLM must fill.
    agents_def.py       Five thin agent classes: research loops + LLM calls.
    persist.py          THE ONLY writer of evidence/findings/recs rows ->
                        provenance rules enforced in exactly one place.
    handlers.py         Mission handlers: baseline graph, on-demand dispatch,
                        execute_agent_run() (run row lifecycle + persistence).
    experiments.py      Control-vs-treatment buyer cohorts + lift scoring.
    usage via UsageEvent rows written by executor + run lifecycle.

  tools/                WEB CAPABILITIES (PRD_3 §13 SEARCH/READ):
    base.py             SearchHit/FetchResult/ToolObservation types +
                        AGENT_CAPABILITIES permission matrix (§30).
    composio_plane.py   Live plane: Composio REST v3.1 no-auth search toolkit.
    mock_plane.py       Deterministic seeded corpus (offline demo/tests).
    router.py           ToolRouter: per-agent allowlist enforcement + run
                        budget metering + observation log -> evidence source.

  llm/                  provider.py (interface) + openai_compat.py
                        (OpenRouter/OpenAI/Groq/anything compatible) +
                        factory.py (process-wide singleton).

  memory/               interface.py (MemoryStore) + mem0_adapter.py (live
                        REST) + local_adapter.py (Postgres keyword fallback).

  gateway/ transactions/ agent/ demo/ adapters/ onboarding/
                        INTACT Legacy V0 commerce stack. Untouched. Reachable
                        under the V0 nav dropdown. Policy engine may become
                        experiment/action infrastructure later.

apps/web/src/
  styles.css            Design tokens extracted from aside.com's compiled CSS
                        (see §13). Tailwind v4 @theme + small hand CSS layer.
  components/kit.tsx    New design system primitives (Eyebrow, SectionHead,
                        StatusChip, DiffBadge, SimulatedTag, StatBar...).
  components/ui.tsx     OLD Terminal-Fintech components — kept because the five
                        legacy pages import them.
  lib/team.ts           Typed client for /api/v1/team/* + EventSource helper.
  pages/OnboardingPage.tsx    Hero + URL/goal form + sample task-feed cards.
  pages/BaselinePage.tsx      Live SSE activity feed + baseline summary.
  pages/TeamPage.tsx          Agent cards + launch mission + mission lists.
  pages/MissionDetailPage.tsx Runs/findings/recs drill-down + budget meter.
  pages/StrategyPage.tsx      Ranked recommendations + experiment console.
  pages/{Connect,Profile,Policies,Console,Trace}*.tsx   Legacy V0 pages.
```

---

## 4. Data Model

All models use String(36) UUID PKs (SQLite-testable), JSONB-with-SQLite-variant
columns, TimestampMixin. One models file, one additive migration (0003).

```
merchants (V0)  1<------*  merchant_profiles   (goal, category, competitors...)
     |      1<------*  merchant_sources    (observed URLs, kinds)
     |
     |*<-------------1  baseline_snapshots  (versioned vN per merchant, UNIQUE)
     |
     |*<-------------*  missions             THE central object
     |                     | 1
     |                     |*
     |                agent_runs           parent_run_id + depth = fan-out tree
     |                     | 1
     |        +------------+------------+
     |        v            v             v
     |    evidence      findings     recommendations
     |   (claims,     (cite ev ids)  (cite finding ids,
     |    urls,                        priority_rank, is_hypothesis)
     |    fact/inference/speculation)
     |
     |*<-------------*  experiments  1<--*  experiment_runs (arm, selected)
     |
     |*<-------------*  usage_events       (mission_created, agent_run, ...)
     |*<-------------*  memory_refs        (audit trail of semantic memories)

strategy_agents   registry of the 5 specialists' contracts (introspectable)
```

Why JSON-array link columns (`evidence_ids_json`, `finding_ids_json`) instead
of join tables? MVP trade-off: provenance chains are always read together with
their owner row, never queried inversely at scale. Normalizing later is a
migration; premature join tables now would slow the demo build. The IDs are
real FKs logically — integrity is enforced by `persist.py` being the only
writer.

Key columns with intent:

- `missions.runs_used` + `budget_runs` — atomic budget claiming happens via
  `UPDATE ... WHERE runs_used < budget_runs` (race-free, see bug #12).
- `agent_runs.parent_run_id` + `depth` — implements the §11 star fan-out;
  depth > 0 renders as an indented child row in the UI.
- `evidence.epistemic_state` — fact|inference|speculation, surfaced in the API
  payload and rendered as colored chips (§28 rule).
- `recommendations.is_hypothesis` — auto-set true when a recommendation cites
  zero findings (§16: no-evidence conclusions may exist only as hypotheses).
- `experiments.is_simulated` — ALWAYS true in MVP; result_json additionally
  embeds `"SIMULATED": true` and a "NOT real production revenue" note.

---

## 5. Mission Engine

### 5.1 Lifecycle

```
MISSION:  CREATED -> QUEUED -> RUNNING --+--> COMPLETED
                              |          |--> PARTIALLY_COMPLETED (budget stop)
                              |          |--> FAILED
                              |          |--> CANCELLED  (cooperative signal)
                              |          +--> TIMED_OUT   (wall clock)
                              +--(cancel while queued)--> CANCELLED

RUN:      PENDING -> RUNNING -> COMPLETED | FAILED | CANCELLED | TIMED_OUT
```

Rules enforced in code:

- Transitions use `UPDATE ... WHERE status IN (expected)`; if rowcount=0 you
  lost a race and must stand down. This makes duplicate workers harmless
  (bug #14 test: executing the same mission twice cannot double-run).
- Budget exhaustion is NOT failure: stop spawning, synthesize what exists,
  finish as PARTIALLY_COMPLETED (§36).
- Cancellation is cooperative: `ctx.ensure_not_cancelled()` raises
  `MissionCancelled` between steps/tool calls; the executor also polls the
  cancel signal once per second around the handler task.

### 5.2 Why two cancellation exception types?

A raw `asyncio.CancelledError` raised inside a coroutine means "unwind NOW" —
re-raising it out of `execute_mission` would kill the worker task instead of
returning a clean status. So handlers raise the dedicated `MissionCancelled`
(bug #19), which the executor converts to status=CANCELLED without unwinding
anything else. Real external cancellation still uses asyncio.CancelledError.

### 5.3 Queue drivers

One interface, two implementations, chosen by config:

```
REDIS_URL set                      REDIS_URL empty (dev/test default)
+--------------------------+      +-------------------------------+
| ready: LIST              |      | asyncio.Queue (maxsize 4096)  |
| leased: ZSET score=expiry|      | cancelled: set                |
| cancel: SET              |      | in_flight: set                |
+--------------------------+      +-------------------------------+
Lua CLAIM script (atomic):        claim() = get_nowait(); no leases needed
  1. recover expired leases       (single process); same cancel semantics.
  2. pop candidate; drop if
     cancelled; ZADD lease
Heartbeat: Lua ZADD GT XX extends lease if still owned.
Crash recovery: expired lease scores return jobs to ready automatically.
```

WHY a process-wide singleton for the in-process driver (bug #7): producers
(API route enqueueing) and the consumer (embedded worker) must share the SAME
queue object. `build_queue()` returning fresh instances silently forked the
queue — missions were enqueued into a queue nobody consumed.

WHY Lua for Redis claim: pop-from-list + ownership-write + cancel-filter must
be atomic across N replica workers; anything less races.

### 5.4 Admission control

Before RUNNING, a mission must acquire two counted slots:

```
global limiter  (max_concurrent_missions_global = 4)
merchant limiter (max_concurrent_missions_per_merchant = 2)
```

If either fails, the job is RELEASED back to the ready list (backpressure, not
rejection). Both release paths live in a `finally` so crashes can't leak slots.
Counters are Redis-backed in production, asyncio-lock-backed locally.

### 5.5 Budgets

Two levels, both config-driven (§36):

- Mission level: `budget_runs` — claimed ATOMICALLY before creating each
  AgentRun row (bug #12: count-then-create raced under parallel fan-out and
  overshot budgets; fixed with conditional UPDATE).
- Run level: `budget_tool_calls` — ToolRouter meters every capability call and
  raises BudgetExhausted; agents catch it and summarize partial results.

### 5.6 Timeouts

Three nested ceilings: LLM request timeout (90s) < agent-run timeout (300s,
enforced with `asyncio.wait_for` + a cancellation-poll wrapper) < mission
timeout (900s, min'd with the row's own max_runtime_seconds). No unit can hang
forever; nothing is ever left in RUNNING.

### 5.7 Progress events

ProgressBus publishes JSON events per mission: `mission_status`, `run_status`,
`tool_call`, `log`. Local subscribers are asyncio.Queues; when Redis is
configured events ALSO publish to `acg:progress:{mission_id}` pub/sub so API
and worker processes can live apart. Events are transient — history comes from
DB rows when a finished mission is opened.

---

## 6. Agent Runtime

### 6.1 The contract (PRD_3 §8)

```python
AgentContract(name, key, role, purpose, allowed_tools, mission_types, ...)
BaseSpecialistAgent.execute(ctx) -> AgentRunResult(pydantic)
```

Every output passes pydantic validation; malformed output gets ONE repair round
(the validation error text is fed back to the model); failing twice raises
OutputValidationError -> run FAILED with structured error. Malformed outputs
can never poison evidence tables.

### 6.2 RunContext

Everything one run may touch, injected per execution:

```
RunContext(mission_id, run_id, merchant_id, agent_key, objective,
           depth, parent_run_id, contract,
           budget_tool_calls, deadline_seconds,
           memory, llm, tools=ToolRouter, merchant_context, extra)
```

Nothing is imported globally by agents — swapping providers is config change,
not code change. Tests inject fakes through four tiny accessor functions in
handlers (`_get_llm/_get_memory/_get_plane/_session_factory`) — monkeypatch
targets documented as test seams.

### 6.3 The five specialists

| key | class | research loop | output schema |
|---|---|---|---|
| market | MarketIntelligenceAgent | trends_search + news_search + web_search (+fetch×3) | ResearchOutput |
| competitor | CompetitorIntelligenceAgent | shopping_search + web_search (+fetch×3) | ResearchOutput |
| presence | PresenceAgent | web_search brand + news_search (+fetch×3) | ResearchOutput |
| buyer | BuyerSimulationAgent | shopping_search + fetch product pages | BuyerSimulationOutput |
| strategy | StrategyAgent | NO web tools — reads findings block + memory only | StrategySynthesisOutput |

Buyer persona and buyer_mission arrive via `extra`; Strategy receives the
findings block rendered from DB rows plus an evidence-count note.

Findings cite claims by index (`claim_indexes`) — persist.py resolves indexes
to real Evidence UUIDs, guaranteeing every finding carries provenance or does
not exist.

---

## 7. Tool Plane

### 7.1 Capability matrix (PRD_3 §30, encoded in tools/base.py)

```
capability        market  competitor  buyer  presence  strategy
web_search          Y         Y         Y       Y         -
news_search         Y         Y         -       Y         -
shopping_search     -         Y         Y       -         -
trends_search       Y         -         -       -         -
fetch_url           Y         Y         Y       Y         -
memory (via ctx)    Y         Y         Y       Y         Y
```

ToolRouter denies disallowed capabilities with CapabilityDenied BEFORE burning
budget. The Strategy Agent is intentionally web-blind: it reasons over stored
evidence only (§30), which is what makes its recommendations traceable.

### 7.2 Two planes, one interface

```
COMPOSIO_API_KEY set + enabled          otherwise / construction failure
ComposioToolPlane                        MockToolPlane
REST v3.1 backend.composio.dev           deterministic seeded corpus
tools used (all no-auth):                (GearUp Cycles / Velocity Sports
  COMPOSIO_SEARCH_WEB                    fiction so demos + tests run offline
  COMPOSIO_SEARCH_NEWS                   and produce meaningful evidence)
  COMPOSIO_SEARCH_SHOPPING
  COMPOSIO_SEARCH_TRENDS
  COMPOSIO_SEARCH_FETCH_URL_CONTENT
```

Every observation becomes a ToolObservation{capability, target, ok, hits,
text, latency} logged on the router; persist.py converts observations into
Evidence rows — including FAILED ones (marked `[FAILED: reason]`), which is
how the system honestly reports "shopping search returned zero results"
instead of inventing data.

Response-shape notes learned live (see bug #16): NEWS/DDG return flat lists;
SHOPPING nests `results.categorized_shopping_results[].shopping_results[]`
with `product_link`/`price` fields — parser written against the real shape,
plus a dict-flattening defense in `_serp_like`.

---

## 8. LLM Layer

```
STRATEGY_LLM_BASE_URL=https://openrouter.ai/api/v1
STRATEGY_LLM_MODEL=stealth/ox-alpha          <- primary (your preference)
STRATEGY_LLM_FALLBACK_MODELS=z-ai/glm-5.2:free
```

`OpenAICompatProvider.generate()` walks the chain, `_CHAIN_PASSES=2` rounds ×
`_ATTEMPTS_PER_MODEL=3` attempts, retrying 408/425/429/5xx/network errors with
long-jitter backoff (`min(3·2^n, 30s)` + up to 50% jitter, honoring Retry-After).
Free/shared pools throttle in bursts — thin retry policies fail whole missions
(bug #15).

`structured_generate(schema)`:

```
system hint = schema JSON + "concise strings, <=4 claims, <=3 findings"
attempt 1 -> parse (_extract_json strips fences, finds outermost braces)
   fail -> attempt 2 appends the validation error as a correction user msg
   fail -> ValueError -> OutputValidationError upstream -> run FAILED
empty completions raise immediately (stealth model sometimes burns the whole
token budget on whitespace; retried rather than accepted)
max_tokens default 4000 (2000 truncated mid-JSON on verbose summaries — bug #17)
```

A global semaphore caps in-flight LLM calls (`max_llm_concurrency_global=8`).

---

## 9. Memory Layer

```
MemoryStore iface:  add(merchant_id, text, kind) / search(q, k) / close()
      |                                   |
Mem0Adapter (live REST)            LocalMemoryAdapter (fallback)
  POST /v1/memories/ (async ingest)  keyword-overlap + recency score over
  POST /v2/memories/search/          MemoryRef.text_preview rows
  user_id = "merchant-{uuid}"        same interface, swap = config change
```

Both are audited via `MemoryRef` rows in Postgres (provider + provider_memory_id
unique pair). Mem0 ingestion is asynchronous upstream — `add` accepts instantly
and becomes searchable within seconds; callers never block on it. Verified live:
stored a competitor fact, retrieved it semantically at score 0.25, then cleaned up.

---

## 10. Baseline Graph

```
onboard(url, goal)
   -> workspace (merchant + profile v0) + BASELINE mission queued

BASELINE HANDLER (intel/handlers._baseline_handler)
   Phase A (parallel, sem=3):
     market  --\                                  each: tools -> LLM ->
     competitor -> gather findings/evidence       persist obs+findings
     presence --/
   Phase B: buyer simulation (persona fan-out, depth=1 children)
   Phase C: strategy synthesis over ALL findings -> recommendations
   -> BaselineSnapshot vN (UNIQUE merchant_id+version) written even on
      partial success; health_score = ok_phases/total
```

Chicken-and-egg note: profiles start empty (the baseline IS what fills them),
so first-run queries lean on URL/objective keywords. Re-runs benefit from the
now-populated profile + memory.

---

## 11. Experiment Engine

```
POST /experiments  -> Experiment(CREATED, SIMULATED=true)
POST /experiments/{id}/start
   -> Mission(type=experiment) linked via experiment.mission_id, queued

EXPERIMENT HANDLER
   cohort_size N (capped by remaining mission budget)
   for i in 0..N-1:
      buyer run (CONTROL messaging)   \
      buyer run (TREATMENT messaging)  } paired cohort, parallel, bounded
   selection scored by matching the chosen product label per arm
   rates + relative lift persisted into experiment.result_json
   EVERY artifact carries SIMULATED=true + explicit non-production note
```

Experiments run THROUGH the mission engine so budgets/usage accounting stay
uniform. Live verified: control 33% vs treatment 100% (+200% simulated lift)
across 3+3 genuine LLM buyer runs.

---

## 12. Complete API Reference

Base: `/api/v1/team`. Errors: `{"detail": {"code", "message"}}`.

### Onboarding

| Endpoint | Method | Notes |
|---|---|---|
| `/team/onboard` | POST | `{url, goal?, name?, skip_baseline?}` → creates merchant workspace + profile v0; queues baseline unless `skip_baseline` (loadtests/tests). Idempotent per normalized slug: re-onboarding returns existing workspace with `created:false`. URL auto-prepends https://, validated for plausibility. |

### Missions

| Endpoint | Method | Notes |
|---|---|---|
| `/team/missions` | POST | `{merchant_id,name,objective,mission_type?,priority?,budget_runs?}`. Type validated against registered handlers. Idempotency-Key header → replay-safe creation. Enqueues onto the shared queue. |
| `/team/missions/{id}` | GET | Full detail incl. `runs_used/budget_runs`, timestamps, result_summary_json, error_json. |
| `/team/missions/{id}/runs` | GET | AgentRun rows ordered by creation (parent then children by depth). |
| `/team/missions/{id}/intel` | GET | Findings (each w/ resolved evidence briefs) + ranked recommendations for the mission. |
| `/team/missions/{id}/events` | GET (SSE) | Live stream: mission_status / run_status / tool_call / log frames. EventSource-friendly. |
| `/team/missions/{id}/cancel` | POST | Sets cancel signal; queued missions flip straight to CANCELLED; running ones unwind cooperatively. 409 ALREADY_TERMINAL if done. |
| `/team/merchants/{id}/missions` | GET | Paginated list, optional `status` filter. |

### Intelligence

| Endpoint | Method | Notes |
|---|---|---|
| `/team/merchants/{id}/recommendations` | GET | Cross-mission strategic queue, newest first, optional status filter. |
| `/team/experiments` | POST | Create (Idempotency-Key aware). Always SIMULATED. |
| `/team/experiments/{id}/start` | POST | Creates+queues the linked mission. 409 if not CREATED/FAILED. |
| `/team/experiments/{id}` | GET | Status + result_json + arm tallies. |
| `/team/merchants/{id}/experiments` | GET | List with per-arm run counts. |

### Meta

| Endpoint | Method | Notes |
|---|---|---|
| `/team/meta` | GET | Driver, provider readiness (llm/composio/mem0), registered mission types, effective limits — the UI header chip reads this. |

Legacy V0 surface (`/api/v1/onboarding/*`, `/api/v1/merchants/{slug}/...`,
`/api/v1/transactions/*`, `/api/v1/agent/*`, `/api/v1/demo/*`,
`/api/v1/payments/*`, `/api/v1/metrics`) remains mounted behind
`ENABLE_LEGACY_ROUTES=true` and is untouched.

---

## 13. Frontend

**Borrowed from aside.com** (extracted from their compiled CSS — ground truth,
not guesses): page `#0a0a0a`, card `#171717`, foreground `#fafafa`, muted
`#a1a1a1`, borders white/10 & white/20, ring `#737373`, radius `.625rem`,
brand sky `#00bcfe`/`#00a5ef`. Typography ROLES: display face for headlines
(Space Grotesk standing in for their licensed AsideDisplay), clean sans body,
mono for metadata (their Geist-Mono role → JetBrains Mono). Their copy system:
eyebrow-in-brackets small caps → sentence-case headline → one-line support →
proof; blunt, brand-as-actor voice ("Your team studies…").

**Rejected from aside.com** (not our product): marketing IA, pricing/download
CTAs, YC badge, footer link columns, feature pages.

Mapping of their signature patterns to ours:

| Aside pattern | Our use |
|---|---|
| Hero browser mockup + recent-tasks feed | Onboarding sample task-card stack ("your team at work") |
| Task cards (status/duration/title/snippet/timestamp/schedule tag) | MissionRow + baseline feed lines |
| Benchmark horizontal bars | Experiment control-vs-treatment rate bars |
| "+6 −0" diff badges | Evidence-count badges on findings |
| Eyebrow → H2 → support → proof | Every section via `<SectionHead>` |
| Final CTA band | "Launch a mission" footer band |
| Masked headline device | Skipped (security-copy gimmick, not ours) |

Pages/state: `App.tsx` holds `{page, merchantId, missionId}`; merchant id
persists in localStorage (`acs.merchantId`); legacy pages render unchanged
inside the new Shell under the V0 dropdown. SSE consumption via
`streamMissionEvents` (EventSource, auto-reconnect; finished missions simply
go quiet).

---

## 14. Bug Log — Every Problem We Hit

Chronological, with root cause and the lesson. These are the "why" behind
several non-obvious pieces of code.

1. **Alembic autogen tried to DROP `demo_overrides`.**
   Cause: `DemoOverride` lives in `db/demo_overrides.py`, never imported by
   `migrations/env.py`, so it was absent from `Base.metadata` — autogen saw a
   table it didn't know and wrote a drop. Fix: import it in env.py AND strip
   the destructive + cosmetic alter_column noise from the generated migration.
   Lesson: autogenerate is only as good as the metadata you feed it; review
   diffs before applying.

2. **Async URL mangling: `sqlite+aiosqlite+aiosqlite://`.**
   Cause: chained `.replace("sqlite://", ...)` — the string "aiosqlite://"
   CONTAINS "sqlite://" as a substring, so the second replace ate the first's
   output. Fix: prefix surgery (`"sqlite+aiosqlite" + url[len("sqlite"): ]`).
   Lesson: str.replace chaining on overlapping patterns is a trap.

3. **pytest fixtures not found / plugin mixing.**
   Cause: async fixtures placed in `tests/conftest_async.py` (wrong name —
   pytest only loads `conftest.py`), plus mixing anyio marks with
   pytest-asyncio fixtures. Fix: everything in conftest.py, standardize on
   pytest-asyncio `asyncio_mode="auto"`.

4. **In-memory SQLite: executor saw an empty database.**
   Two layers: (a) the executor bound the GLOBAL engine (Postgres) while tests
   seeded a private engine → fixed with dependency injection
   (`session_factory=` param); (b) `sqlite://` in-memory gives each pooled
   connection its OWN database → StaticPool initially, later NullPool over a
   temp FILE db so multiple concurrent sessions behave like production.

5. **Stale identity-map reads in tests.**
   Bulk `update()` doesn't reliably refresh already-loaded objects
   (`expire_on_commit=False`). Engine was correct; assertions weren't. Fix:
   `await async_db.refresh(obj)` before asserting.

6. **Test suite took 3m42s.**
   Cause: concurrent sessions on one SQLite file serialized with default busy
   handling. Fix: `PRAGMA journal_mode=WAL` + `busy_timeout=15000` +
   `synchronous=NORMAL` on connect. Suite dropped to ~5s for the same work.
   Lesson: match test DB behavior to production concurrency semantics.

7. **Missions queued forever after CP1 smoke.**
   TWO bugs compounding: (a) the API route created missions but NEVER called
   enqueue; (b) each `build_queue()` constructed a NEW InProcessJobQueue, so
   producer and consumer held different queues. Fix: `enqueue_mission` wired
   into onboard/create routes + process-wide queue singleton.
   Lesson: integration smoke tests against a running server exist precisely to
   catch seams unit tests structurally cannot.

8. **`pkill -f "uvicorn app.main:app"` killed the bash tool itself.**
   Cause: `-f` matches FULL cmdlines — the wrapping `bash -c 'pkill -f ...'`
   contained the pattern too. Fix: bracket trick `pkill -f "[u]vicorn..."`.
   Several mysterious shell-timeouts were this.

9. **`'async_sessionmaker' object does not support async CM` (production!).**
   Cause: handlers `_session_factory()` returned the SESSIONMAKER while call
   sites did `async with _session_factory() as db:`. Tests passed because the
   monkeypatched seam returned a real session — the production seam had zero
   coverage. Fix: return `AsyncSessionLocal()` (an actual AsyncSession).
   Lesson: when a test seam replaces a factory, keep one production-shaped test
   that exercises the real accessor.

10. **`'tags' is an invalid keyword argument for Finding`.**
    Column is named `tags_json`; persist.py passed `tags=`. Renamed at call
    site. Lesson: ORM kwargs ≠ schema field names; keep them aligned or alias.

11. **"Session is already flushing" under parallel phases (tests).**
    Three concurrent agent runs shared ONE AsyncSession via the old test seam.
    Invalid by design. Fix: session_factory fixture creates FRESH sessions per
    acquisition (NullPool file-db), mirroring production connection semantics.

12. **Budget overshoot under fan-out (5 runs on a 3-run budget).**
    Count-then-create checked remaining budget, then INSERTed — three parallel
    tasks all passed the check before any committed. Fix: atomic conditional
    claim `UPDATE missions SET runs_used = runs_used+1 WHERE runs_used <
    budget_runs`; rowcount 0 ⇒ denied. Also added pre-phase budget checks so
    the baseline stops gracefully and writes a partial snapshot.

13. **On-demand missions ran the DEFAULT fleet, ignoring assignments.**
    Executor never copied `mission.agent_assignments_json` into
    `ctx.artifacts`; the handler's lookup always found nothing. Fix: executor
    populates `artifacts["agent_assignments"]`. Test asserts assigned agents
    ran and unassigned didn't.

14. **Duplicate execution safety.** Guaranteed by the conditional-transition
    design (second `execute_mission` sees status != QUEUED and stands down).
    Locked in by a dedicated test.

15. **OpenRouter 429 storms failed whole baselines.**
    Both free-pool models throttled simultaneously; original 3×~8s retries
    exhausted in <30s. Fix: 2 full chain passes × 3 attempts, backoff to ~45s
    with jitter, Retry-After honored. Missions now ride out multi-minute
    throttle windows.

16. **Composio SHOPPING response shape.**
    News/DDG return flat result lists; shopping nests
    `results.categorized_shopping_results[].shopping_results[]` with
    `product_link`/`price`/`extensions`. Original generic parser crashed
    (`slice(None,10,None)` — slicing a dict). Fix: dedicated parser for the
    real shape + defensive dict-flattening in the generic path.

17. **Structured JSON truncation + empty completions.**
    Verbose summaries blew the 2000-token cap mid-JSON; stealth model
    sometimes returned whitespace-only content. Fix: max_tokens 4000,
    conciseness instructions in the schema hint ("summary <50 words"),
    empty-text treated as immediate retryable error.

18. **Market agent TIMED_OUT at 240s.** Patient LLM retries legitimately need
    longer than the old run ceiling. Fix: `AGENT_RUN_TIMEOUT_SECONDS=300`.

19. **Cooperative cancel killed the worker task.** Handlers raised raw
    `asyncio.CancelledError`; executor re-raised it and the awaiting caller
    died instead of receiving status=CANCELLED. Fix: dedicated
    `MissionCancelled` exception type (see §5.2).

20. **`exp.id` was None in the idempotency snapshot.** SQLAlchemy assigns
    Python-side defaults at flush; the response dict was built BEFORE flush.
    Fix: flush → build snapshot → insert key → commit.

21. **`skip_baseline` workspaces silently vanished.** That path never called
    commit (only the create_mission path did); session close rolled the
    merchant back, so the follow-up missions POST 404'd MERCHANT_NOT_FOUND.
    Fix: explicit commit on the skip path.

22. **Loadtest starved its own stub missions.** Onboard auto-queued heavy REAL
    baselines which occupied all 4 worker slots for minutes; stubs sat QUEUED.
    Fix: `skip_baseline:true` flag + harness updated. Lesson: load tests must
    measure the unit you intend, not incidental work.

23. **Embedded worker died mid-run, queue stalled forever.** Observed worker
    "stopped" log mid-loadtest; unretrieved task exceptions and signal timing
    made the single worker a single point of death. Fixes: (a) catch-all
    around the worker loop body (log + continue, never die mid-loop);
    (b) lifespan supervisor restarts a crashed worker after 2s; (c) drain
    logic preserved for clean shutdowns.

24. **500-job burst → HTTP 500s.** InProcessJobQueue `maxsize=256` threw
    QueueFull on overflow. Fix: 4096 (backpressure belongs to admission
    control, not silent queue rejection). Re-measured: 412/500 drained within
    the window at ~2/s, zero corruption.

25. **Ruff E501/format churn.** Adopted `ruff format` alongside `check --fix`
    as a mandatory final step per milestone; several edits above were applied
    via python scripts precisely because ruff had reformatted anchors.

---

## 15. Testing Strategy

99 tests, three layers:

```
UNIT        queue contract, capability matrix/denial, budget metering,
            graceful tool degradation, settings bounds, memory roundtrip
INTEGRATION FastAPI -> engine -> queue -> DB via httpx ASGI transport;
            dependency-overridden sessions; embedded worker disabled
SCENARIO    §34 matrix: simple mission, timeout, cancel-queued,
            cooperative-cancel-running, unknown-handler failure,
            duplicate execution, budget exhaustion, baseline e2e
            traceability, on-demand routing, experiment arms + labels
```

Test-seam philosophy: production code exposes tiny accessors
(`_get_llm/_get_memory/_get_plane/_session_factory`) purely as documented
monkeypatch points; no `if TEST_MODE` branches anywhere. FakeLLM returns
schema-valid canned outputs keyed by schema type; MockToolPlane provides a
seeded corpus so scenario tests exercise the full handler path offline.

Fixture stack: `db_env` builds a per-test FILE SQLite engine (NullPool + WAL
pragmas) exposing both a long-lived assertion session AND a fresh-session
factory injected into the executor/handlers — mirroring production's
connection-per-unit semantics (bugs #4/#6/#11).

---

## 16. Operations Runbook

```bash
cp .env.example .env        # STRATEGY_LLM_* required for real agents;
                            # COMPOSIO_API_KEY / MEM0_API_KEY recommended
docker compose up --build   # postgres :5439, redis :6380, api :8000, web :5173
cd apps/api && alembic upgrade head   # (compose does this automatically)
python -m pytest -q                   # 99 tests
python -m scripts.loadtest --levels "1x1,1x10,1x25,5x25,10x50"
```

Env knobs that matter (all in core/config.py, override via .env):

| Var | Default | Meaning |
|---|---|---|
| REDIS_URL | "" (in-process) | set for multi-process/multi-worker |
| EMBEDDED_WORKER | true | API runs workers internally (demo mode); false for replica deployments |
| WORKER_CONCURRENCY | 4 | parallel missions per worker |
| MAX_CONCURRENT_MISSIONS_GLOBAL / _PER_MERCHANT | 4 / 2 | admission control |
| MAX_AGENT_RUNS_PER_MISSION | 25 | hard ceiling (§11) |
| MAX_SUB_AGENT_DEPTH / MAX_CHILDREN_PER_PARENT | 1 / 5 | fan-out bounds |
| AGENT_RUN_TIMEOUT_SECONDS / MISSION_TIMEOUT_SECONDS | 300 / 900 | wall clocks |
| STRATEGY_LLM_FALLBACK_MODELS | z-ai/glm-5.2:free | try-in-order chain |

Scaling path (no rewrite): raise worker concurrency → switch REDIS_URL on →
run `python -m app.engine.worker` as N replicas (lease semantics make them
safe) → scale API replicas. Measured local envelope lives in
`docs/scaling.md`.

---

## 17. Known Gaps & Honest Caveats

- **First-run profile cold-start:** baseline queries lean on URL/objective
  keywords because the profile fills only AFTER the baseline. Store-name
  collisions (real case: gearupcycles.in vs US namesakes) are detected and
  reported as findings — correct behavior, but richer onboarding inputs would
  sharpen queries.
- **Shopping visibility for fictional stores** is genuinely zero; the system
  reports it honestly instead of hallucinating. Demo with a store that has
  real market presence for the richest story.
- **Recurring missions** have handler support (`recurring` maps to the
  on-demand dispatcher) but no scheduler yet — PRD allows simple Redis-backed
  scheduling later.
- **Evidence drill-down in UI** shows briefs (claim/URL/state) inline; a
  dedicated evidence explorer page is unbuilt.
- **Auth** remains V0 scaffolding; the strategy surface assumes a single
  trusted operator, consistent with "working product demo" scope.
- **Root `web/` scaffold** deletion was proposed in planning; still present
  and harmless — decide before committing.

---

*Generated during the CP3 walkthrough. Numbers in docs/scaling.md were
measured, not estimated; every claim above traces to code in the repo.*
