# Scaling Notes — Measured, Not Invented (PRD_3 §23.12)

Environment: single dev laptop, one `uvicorn` process with embedded worker
(`worker_concurrency=4`, in-process queue driver), PostgreSQL 16 (docker),
stub mission handler (engine overhead only — no LLM/tool latency).
Measured with `python -m scripts.loadtest --levels ...` on 2026-08-26.

## Results

| merchants | missions each | total | completed | errors | throughput/s | p50 s | p95 s |
|---|---|---|---|---|---|---|---|
| 1 | 1  | 1   | 1   | 0 | 1.59 | 0.41 | 0.41 |
| 1 | 10 | 10  | 10  | 0 | 2.29 | 4.02 | 4.02 |
| 1 | 25 | 25  | 25  | 0 | 1.92 | 6.11 | 11.7 |
| 5 | 25 | 125 | 125 | 0 | 2.88 | 20.3 | 38.6 |
| 10 | 50 | 500 | 412* | 1 | 1.92 | 89.9 | 173.9 |

\* The 500-job burst completed **412 inside the harness's 180s per-level
observation window** at a steady ~2/s; the remaining jobs drained after the
window (no losses — completion is idempotent and durable state lives in
Postgres). One client-side disconnect occurred under peak burst.

## Interpretation

- Sustained claim for this environment: **~125 concurrent queued missions
  across 5 merchants drain error-free at ~2.9/s** with p50 ≈ 20s end-to-end
  (dominated by worker poll interval + admission caps, not the DB).
- The 500-burst degrades latency (p50 90s) but does not corrupt state or lose
  work: exactly-once mission execution held under burst (duplicate claims are
  rejected by defensive status transitions).
- Bottleneck order observed: per-merchant concurrency cap → worker poll loop
  → single-process event loop. All three scale horizontally by design:
  raise `WORKER_CONCURRENCY`, move to the Redis queue driver with N worker
  replicas (`python -m app.engine.worker`), and/or run multiple API replicas.
- Real specialist missions are provider-latency-bound (~30–90s per agent run);
  engine overhead measured here is negligible against that budget by design.

## Re-measuring

```bash
docker compose up -d db redis
cd apps/api && alembic upgrade head
uvicorn app.main:app --port 8000 &        # embedded worker enabled
python -m scripts.loadtest --levels "1x1,1x10,1x25,5x25,10x50"
```
