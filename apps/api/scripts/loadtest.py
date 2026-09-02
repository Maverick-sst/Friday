"""Progressive load harness for the mission engine (PRD_3 §23.12).

Runs N merchants x M concurrent missions against the live API using the
zero-cost `stub` handler so results measure ENGINE overhead, not provider
latency. Reports throughput, p50/p95 latency, error rate and queue depth.

Usage:
    python -m scripts.loadtest --levels 1x1 1x10 1x25 5x25
    python -m scripts.loadtest --api http://localhost:8000
"""

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime

import httpx

LEVELS = [(1, 1), (1, 10), (1, 25), (5, 25), (10, 50)]


async def run_level(client: httpx.AsyncClient, merchants: int, missions: int) -> dict:
    latencies: list[float] = []
    errors = 0
    started = time.monotonic()

    async def _one(m_idx: int) -> None:
        nonlocal errors
        try:
            r = await client.post(
                "/api/v1/team/onboard",
                json={
                    "url": f"https://loadtest-{m_idx}-{int(time.time())}.example.com",
                    "skip_baseline": True,
                },
            )
            r.raise_for_status()
            mid = r.json()["merchant_id"]
            tasks = []
            for j in range(missions):
                tasks.append(
                    client.post(
                        "/api/v1/team/missions",
                        json={
                            "merchant_id": mid,
                            "name": f"lt-{j}",
                            "objective": f"load test mission {j}",
                            "mission_type": "stub",
                        },
                        headers={"Idempotency-Key": f"lt-{m_idx}-{j}-{started}"},
                    )
                )
            responses = await asyncio.gather(*tasks)
            ids = []
            for resp in responses:
                resp.raise_for_status()
                ids.append(resp.json()["mission_id"])

            t0 = time.monotonic()
            pending = set(ids)
            while pending and time.monotonic() - t0 < 180:
                await asyncio.sleep(0.4)
                check = await asyncio.gather(*(client.get(f"/api/v1/team/missions/{i}") for i in pending))
                done = {
                    i
                    for i, c in zip(pending, check, strict=False)
                    if c.json().get("status")
                    in ("COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")
                }
                for _i in done:
                    latencies.append(time.monotonic() - t0)
                pending -= done
        except Exception as exc:
            errors += 1
            print(f"    worker error: {exc}")

    await asyncio.gather(*(_one(m) for m in range(merchants)))
    wall = time.monotonic() - started

    def pct(p: float) -> float:
        return (
            round(statistics.quantiles(latencies, n=100)[int(p) - 1], 2)
            if len(latencies) > 10
            else (round(max(latencies), 2) if latencies else 0.0)
        )

    return {
        "merchants": merchants,
        "missions_each": missions,
        "total_missions": merchants * missions,
        "completed": len(latencies),
        "errors": errors,
        "throughput_per_sec": round(len(latencies) / wall, 2) if wall > 0 else 0,
        "p50_s": pct(50),
        "p95_s": pct(95),
        "wall_s": round(wall, 1),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--levels", default=",".join(f"{m}x{n}" for m, n in LEVELS))
    args = parser.parse_args()

    levels = [tuple(map(int, lv.split("x"))) for lv in args.levels.split(",")]
    print(f"load test against {args.api} at {datetime.now(UTC).isoformat()}")
    rows = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        health = await client.get("/healthz")
        health.raise_for_status()
        for merchants, missions in levels:
            print(f"\n== {merchants} merchant(s) x {missions} missions ==")
            row = await run_level(client, merchants, missions)
            rows.append(row)
            print(
                f"   completed={row['completed']}/{row['total_missions']} "
                f"errors={row['errors']} throughput={row['throughput_per_sec']}/s "
                f"p50={row['p50_s']}s p95={row['p95_s']}s wall={row['wall_s']}s"
            )

    print(
        "\n| merchants | missions each | total | completed | errors | throughput/s | p50 s | p95 s | wall s |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['merchants']} | {r['missions_each']} | {r['total_missions']} | "
            f"{r['completed']} | {r['errors']} | {r['throughput_per_sec']} | "
            f"{r['p50_s']} | {r['p95_s']} | {r['wall_s']} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
