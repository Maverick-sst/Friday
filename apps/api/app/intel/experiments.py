"""Counterfactual experiment engine (PRD_3 §19).

    CONTROL (current merchant representation)
        VS
    TREATMENT (proposed improvement)
        |
    Same buyer-mission cohort, run across both arms
        |
    Simulated selection rates + relative lift

Every metric produced here is SIMULATED. Results carry is_simulated=True and
an explicit non-production note; the UI/API must preserve that label (PRD_3
engineering principle 10).
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Experiment, ExperimentRun
from app.engine.context import MissionContext, register_handler

logger = logging.getLogger("acg.intel.experiments")


def _sf():
    """Delegate to the handlers seam at call time (tests monkeypatch there)."""
    from app.intel.handlers import _session_factory

    return _session_factory()


async def load_experiment(db, mission_id: str) -> Experiment | None:
    return await db.scalar(select(Experiment).where(Experiment.mission_id == mission_id))


def _buyer_mission_for_arm(exp: Experiment, arm: str) -> str:
    """Build the purchase mission text for one arm.

    The arm's variant JSON describes how the merchant presents itself
    (messaging copy, delivery promise, returns highlight...). A buyer sees
    exactly one arm's representation; candidates come from tool research.
    """
    variant = exp.control_variant_json if arm == "control" else exp.treatment_variant_json
    presentation = "; ".join(f"{k}: {v}" for k, v in (variant or {}).items() if v)
    text = (
        f"Purchase mission: {exp.hypothesis[:160]} "
        "Research candidate products/stores, compare, then pick one or none."
    )
    if presentation:
        text += (
            f"\n\nOne store you evaluate currently presents itself as: {presentation}. "
            "Judge it exactly as a real buyer would."
        )
    return text


def _merchant_labels(exp: Experiment) -> list[str]:
    """Tokens identifying the merchant's offer across both arms' variants."""
    labels: list[str] = []
    for variant in (exp.control_variant_json, exp.treatment_variant_json):
        for value in (variant or {}).values():
            for token in str(value).replace(":", " ").split():
                clean = token.strip(".,")
                if len(clean) > 3 and clean.lower() not in [x.lower() for x in labels]:
                    labels.append(clean)
    return labels


async def _score_selection(selection: str | None, labels: list[str]) -> bool:
    """Did this buyer's choice reference the merchant's offer?"""
    if not selection:
        return False
    t = selection.lower()
    return any(label.lower() in t for label in labels)


async def _experiment_handler(ctx: MissionContext) -> dict:
    settings = get_settings()
    async with _sf() as db:
        exp = await load_experiment(db, ctx.mission_id)
    if exp is None:
        return {"_status": "FAILED", "summary": "no experiment linked to mission"}

    async with _sf() as db:
        exp_row = await db.get(Experiment, exp.id)
        exp_row.status = "RUNNING"
        exp_row.started_at = datetime.now(UTC)
        await db.commit()

    cohort = max(2, min(exp.cohort_size, max(settings.max_agent_runs_per_mission // 2, 2)))
    labels = _merchant_labels(exp)
    results: dict[str, list[tuple[bool, bool, str | None]]] = {"control": [], "treatment": []}
    sem = asyncio.Semaphore(max(settings.max_concurrent_missions_global, len(labels) and 2))

    async def _one(i: int, arm: str) -> None:
        async with sem:
            await ctx.ensure_not_cancelled()
            res = await execute_agent_run_seam(
                mission_id=ctx.mission_id,
                merchant_id=ctx.merchant_id,
                agent_key="buyer",
                objective=_buyer_mission_for_arm(exp, arm)[:300],
                extra={
                    "persona": f"Cohort buyer #{i + 1}",
                    "buyer_mission": _buyer_mission_for_arm(exp, arm),
                },
            )
            selected = getattr(res.get("result"), "selected", None) if res.get("ok") else None
            picked = await _score_selection(selected, labels)
            results[arm].append((bool(res.get("ok")), picked, selected))

    tasks = [_one(i, arm) for i in range(cohort) for arm in ("control", "treatment")]
    await asyncio.gather(*tasks)

    def _rate(arm: str) -> tuple[float, int]:
        outcomes = results[arm]
        valid = [picked for ok, picked, _ in outcomes if ok]
        if not valid:
            return 0.0, 0
        return sum(valid) / len(valid), len(valid)

    control_rate, control_n = _rate("control")
    treatment_rate, treatment_n = _rate("treatment")
    lift = (
        (treatment_rate - control_rate) / control_rate
        if control_rate > 0
        else (1.0 if treatment_rate > 0 else 0.0)
    )

    async with _sf() as db:
        exp_row = await db.get(Experiment, exp.id)
        for arm in ("control", "treatment"):
            for ok, picked, selected in results[arm]:
                db.add(
                    ExperimentRun(
                        experiment_id=exp.id,
                        arm=arm,
                        buyer_prompt=_buyer_mission_for_arm(exp, arm)[:500],
                        selected=picked if ok else None,
                        selection_json={"choice": selected, "run_ok": ok},
                    )
                )
        exp_row.status = "COMPLETED"
        exp_row.completed_at = datetime.now(UTC)
        exp_row.is_simulated = True  # ALWAYS: simulated metrics only (§19)
        exp_row.result_json = {
            "SIMULATED": True,
            "control_selection_rate": round(control_rate, 4),
            "treatment_selection_rate": round(treatment_rate, 4),
            "simulated_relative_lift_pct": round(lift * 100, 2),
            "cohort_completed": {"control": control_n, "treatment": treatment_n},
            "note": (
                "Simulated counterfactual result from AI buyer simulations. NOT real production revenue."
            ),
        }
        await db.commit()

    logger.info(
        "experiment %s done: control %.0f%% vs treatment %.0f%% (simulated lift %.1f%%)",
        exp.id,
        control_rate * 100,
        treatment_rate * 100,
        lift * 100,
    )
    return {
        "_status": "COMPLETED",
        "SIMULATED": True,
        "control_rate": round(control_rate, 4),
        "treatment_rate": round(treatment_rate, 4),
        "lift_pct": round(lift * 100, 2),
        "summary": (
            f"Simulated A/B: control {control_rate:.0%} vs treatment "
            f"{treatment_rate:.0%} ({lift:+.0%} simulated lift)."
        ),
    }


async def execute_agent_run_seam(**kwargs):
    """Delegate to handlers.execute_agent_run at call time (test seam)."""
    from app.intel.handlers import execute_agent_run

    return await execute_agent_run(**kwargs)


def register_experiment_handler() -> None:
    register_handler("experiment", _experiment_handler)
