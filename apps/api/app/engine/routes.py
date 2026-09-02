"""Strategy-team REST surface (PRD_3 §31): onboarding, missions, SSE progress.

Conventions follow the V0 routers: typed pydantic bodies, GatewayError for
problem responses, idempotency on business-critical creations.
"""

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import GatewayError, conflict, not_found
from app.db.models import Experiment, ExperimentRun, Merchant, MerchantProfile
from app.db.session import get_async_db
from app.engine import service
from app.engine.context import registered_types
from app.engine.progress import ProgressEvent, progress_bus
from app.engine.queue import build_queue
from app.engine.state import (
    MISSION_TERMINAL_STATES,
)

router = APIRouter(prefix="/api/v1/team", tags=["strategy-team"])


# --- Request/response schemas ----------------------------------------------


class OnboardRequest(BaseModel):
    url: str = Field(min_length=4, max_length=1024)
    goal: str | None = Field(default=None, max_length=2000)
    name: str | None = Field(default=None, max_length=255)
    skip_baseline: bool = False  # loadtests/tests: workspace only, no Day-0 run


class MissionCreateRequest(BaseModel):
    merchant_id: str
    name: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=4, max_length=4000)
    mission_type: str = "on_demand"
    priority: Literal["low", "normal", "high"] = "normal"
    budget_runs: int | None = Field(default=None, ge=1, le=200)
    # Optional specialist restriction (e.g. ["buyer"] for the quick buyer sim).
    # Handlers filter unknown keys gracefully, so no hard validation here.
    agent_assignments: list[str] | None = Field(default=None, max_length=12)
    # Structured shopper spec for "shopping" missions (B7): product/size/color/
    # brand/budget. Encoded into the objective as a SHOPPING_SPEC JSON block.
    shopping: dict | None = None


def _normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if " " in url or "." not in url:
        raise GatewayError("INVALID_URL", f"Not a plausible store URL: {raw!r}")
    return url


async def _merchant_or_404(db: AsyncSession, merchant_id: str) -> Merchant:
    merchant = await db.get(Merchant, merchant_id)
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", f"No merchant {merchant_id}")
    return merchant


# --- Onboarding --------------------------------------------------------------


@router.post("/onboard")
async def onboard(body: OnboardRequest, db: AsyncSession = Depends(get_async_db)) -> dict:
    """Create a merchant workspace from a public store URL (PRD_3 §5).

    The baseline analysis mission is created and queued; workers execute it.
    Idempotent per URL: re-onboarding returns the existing workspace.
    """
    url = _normalize_url(body.url)

    slug = url.split("//", 1)[1].split("/", 1)[0].lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-")[:100]

    existing = await db.scalar(select(Merchant).where(Merchant.slug == slug))
    if existing is not None:
        profile = await db.scalar(select(MerchantProfile).where(MerchantProfile.merchant_id == existing.id))
        return {
            "merchant_id": existing.id,
            "slug": existing.slug,
            "created": False,
            "profile_version": profile.version if profile else 0,
            "next": "baseline already provisioned; launch missions",
        }

    merchant = Merchant(
        name=(body.name or slug)[:255],
        slug=slug,
        website_url=url,
        status="active",
    )
    db.add(merchant)
    await db.flush()

    db.add(
        MerchantProfile(
            merchant_id=merchant.id,
            goal_text=body.goal,
            version=0,  # v0 until the baseline engine writes snapshot-backed profile
        )
    )

    if not body.skip_baseline:
        mission = await service.create_mission(
            db,
            merchant_id=merchant.id,
            name=f"Baseline - {merchant.name}",
            objective=(
                f"Run the Day-0 diagnostic baseline for {url}."
                + (f" Merchant goal: {body.goal}" if body.goal else "")
            ),
            mission_type="baseline",
            priority="high",
        )
        await service.enqueue_mission(build_queue(), mission)
    else:
        mission = None
        await db.commit()  # skip_baseline path: persist the workspace explicitly
    return {
        "merchant_id": merchant.id,
        "mission_id": mission.id if mission else None,
        "slug": merchant.slug,
        "created": True,
        "next": "baseline queued; watch /missions/{id}/events"
        if mission
        else "workspace ready (baseline skipped)",
    }


# --- Missions ----------------------------------------------------------------


@router.post("/missions")
async def create_mission(
    body: MissionCreateRequest, request: Request, db: AsyncSession = Depends(get_async_db)
) -> dict:
    """Create + queue a mission. Idempotent via Idempotency-Key header."""
    if body.mission_type not in set(registered_types()) | {"baseline"}:
        raise GatewayError(
            "UNKNOWN_MISSION_TYPE",
            f"Unknown mission type {body.mission_type!r}; registered: {registered_types()}",
        )
    await _merchant_or_404(db, body.merchant_id)

    idem_key = request.headers.get("idempotency-key") or f"auto-{uuid.uuid4().hex}"
    scope = f"merchant:{body.merchant_id}"

    async def _produce() -> dict:
        # Encode the structured shopping spec into the objective (B7) so the
        # handler receives it via ctx.objective — no schema migration needed.
        objective = body.objective
        if body.mission_type == "shopping" and body.shopping:
            import json as _json

            objective = (
                "SHOPPING_SPEC "
                + _json.dumps(body.shopping, default=str)
                + "\n"
                + objective
            )
        mission = await service.create_mission(
            db,
            merchant_id=body.merchant_id,
            name=body.name,
            objective=objective,
            mission_type=body.mission_type,
            priority=body.priority,
            budget_runs=body.budget_runs,
            agent_assignments=body.agent_assignments,
        )
        await service.enqueue_mission(build_queue(), mission)
        return {"mission_id": mission.id, "status": mission.status}

    # Inline idempotency guard (sync core helper works against any session).
    key_full = ("mission:" + scope + ":" + idem_key)[:160]
    from app.db.models import IdempotencyKey

    existing = await db.get(IdempotencyKey, key_full)
    if existing is not None:
        return {**dict(existing.response_snapshot), "idempotent_replay": True}

    result = await _produce()
    db.add(IdempotencyKey(key=key_full, endpoint="POST:/api/v1/team/missions", response_snapshot=result))
    await db.commit()
    return result


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    return {
        "id": mission.id,
        "merchant_id": mission.merchant_id,
        "name": mission.name,
        "objective": mission.objective,
        "mission_type": mission.mission_type,
        "status": mission.status,
        "priority": mission.priority,
        "budget_runs": mission.budget_runs,
        "runs_used": mission.runs_used,
        "tool_calls_used": mission.tool_calls_used,
        "cancel_requested": mission.cancel_requested,
        "started_at": mission.started_at.isoformat() if mission.started_at else None,
        "completed_at": mission.completed_at.isoformat() if mission.completed_at else None,
        "result_summary_json": mission.result_summary_json,
        "error_json": mission.error_json,
    }


@router.get("/merchants/{merchant_id}/missions")
async def list_missions(
    merchant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    limit = max(1, min(limit, 200))
    rows = await service.list_missions(db, merchant_id=merchant_id, status=status, limit=limit, offset=offset)
    return [
        {
            "id": m.id,
            "name": m.name,
            "mission_type": m.mission_type,
            "status": m.status,
            "priority": m.priority,
            "budget_runs": m.budget_runs,
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.post("/missions/{mission_id}/cancel")
async def cancel_mission(mission_id: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    if mission.status in MISSION_TERMINAL_STATES:
        raise conflict("ALREADY_TERMINAL", f"Mission is {mission.status}")
    queue = build_queue()
    try:
        final = await service.cancel_mission(db, queue, mission_id)
    finally:
        await queue.close()
    return {"mission_id": mission_id, "status": final}


@router.get("/missions/{mission_id}/events")
async def stream_events(
    mission_id: str, request: Request, db: AsyncSession = Depends(get_async_db)
) -> StreamingResponse:
    """SSE stream of live mission events (PRD_3 §31 Screen B/D activity feed).

    Fleet PRD A3: connection is replay-safe. The stream yields a DB-backed
    `snapshot` frame first, then the buffered backlog (events published before
    the client connected), then live events. Every frame carries an `id:` line;
    on reconnect EventSource sends Last-Event-ID and the backlog resumes from
    there, so no event is lost or duplicated across reconnects.
    """
    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    from app.db.models import AgentRun

    runs = (
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.mission_id == mission_id)
                .order_by(AgentRun.created_at)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    snapshot = ProgressEvent(
        mission_id=mission_id,
        kind="snapshot",
        payload={
            "mission": {
                "id": mission.id,
                "status": mission.status,
                "mission_type": mission.mission_type,
                "name": mission.name,
                "runs_used": mission.runs_used,
                "budget_runs": mission.budget_runs,
            },
            "runs": [
                {
                    "id": r.id,
                    "agent_key": r.agent_key,
                    "depth": r.depth,
                    "parent_run_id": r.parent_run_id,
                    "status": r.status,
                    "tool_calls_used": r.tool_calls_used,
                    "budget_tool_calls": r.budget_tool_calls,
                    "latency_ms": r.latency_ms,
                }
                for r in runs
            ],
        },
    )

    async def _gen():
        bus = progress_bus()
        last_id = request.headers.get("last-event-id") or ""
        try:
            after_seq = max(int(last_id), 0)
        except ValueError:
            after_seq = 0
        yield ": stream open\n\n"
        try:
            # 1. DB-backed snapshot so a late joiner reconstructs current state.
            yield snapshot.to_sse()
            # 2. Buffered backlog (everything this client missed, if any),
            #    then live events. Both carry sequence ids for Last-Event-ID.
            async for seq, ev in bus.stream(mission_id, after_seq=after_seq):
                if isinstance(ev, ProgressEvent):
                    yield ev.to_sse(seq)
        except asyncio.CancelledError:  # client disconnected
            raise

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/missions/{mission_id}/evidence")
async def get_mission_evidence(
    mission_id: str, db: AsyncSession = Depends(get_async_db)
) -> list[dict]:
    """Evidence briefs for one mission, grouped client-side by agent_run_id
    (Fleet PRD A4: per-run tool provenance in the inspector side panel)."""
    from app.db.models import Evidence

    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    rows = (
        (
            await db.execute(
                select(Evidence)
                .where(Evidence.mission_id == mission_id)
                .order_by(Evidence.created_at)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": e.id,
            "agent_run_id": e.agent_run_id,
            "source_type": e.source_type,
            "source_url": e.source_url,
            "claim": e.claim,
            "excerpt": (e.excerpt or "")[:400],
            "epistemic_state": e.epistemic_state,
            "confidence": e.confidence,
            "observed_at": e.observed_at.isoformat() if e.observed_at else None,
        }
        for e in rows
    ]


# --- Experiments (PRD_3 §19: simulated counterfactuals) ----------------------


class ExperimentCreateRequest(BaseModel):
    merchant_id: str
    hypothesis: str = Field(min_length=8, max_length=2000)
    control_variant_json: dict[str, Any] = Field(default_factory=dict)
    treatment_variant_json: dict[str, Any] = Field(default_factory=dict)
    cohort_size: int = Field(default=10, ge=2, le=50)


@router.post("/experiments")
async def create_experiment(
    body: ExperimentCreateRequest, request: Request, db: AsyncSession = Depends(get_async_db)
) -> dict:
    await _merchant_or_404(db, body.merchant_id)
    idem_key = request.headers.get("idempotency-key") or f"auto-{uuid.uuid4().hex}"
    key_full = ("experiment:" + body.merchant_id + ":" + idem_key)[:160]
    from app.db.models import IdempotencyKey

    existing = await db.get(IdempotencyKey, key_full)
    if existing is not None:
        return {**dict(existing.response_snapshot), "idempotent_replay": True}

    exp = Experiment(
        merchant_id=body.merchant_id,
        hypothesis=body.hypothesis,
        control_variant_json=body.control_variant_json,
        treatment_variant_json=body.treatment_variant_json,
        cohort_size=body.cohort_size,
        status="CREATED",
        is_simulated=True,  # MVP experiments are always simulated (PRD_3 §19)
    )
    db.add(exp)
    await db.flush()
    result = {"experiment_id": exp.id, "status": exp.status, "SIMULATED": True}
    db.add(IdempotencyKey(key=key_full, endpoint="POST:/api/v1/team/experiments", response_snapshot=result))
    await db.commit()
    return result


@router.post("/experiments/{experiment_id}/start")
async def start_experiment(experiment_id: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    """Create + queue the experiment's mission; workers execute both arms."""
    exp = await db.get(Experiment, experiment_id)
    if exp is None:
        raise not_found("EXPERIMENT_NOT_FOUND", f"No experiment {experiment_id}")
    if exp.status not in {"CREATED", "FAILED"}:
        raise conflict("EXPERIMENT_NOT_STARTABLE", f"Experiment is {exp.status}")

    mission = await service.create_mission(
        db,
        merchant_id=exp.merchant_id,
        name=f"Experiment - {exp.hypothesis[:80]}",
        objective=f"Simulated counterfactual test: {exp.hypothesis}",
        mission_type="experiment",
        priority="normal",
        budget_runs=max(exp.cohort_size * 2 + 1, get_settings().max_agent_runs_per_mission),
    )
    exp.mission_id = mission.id
    exp.status = "QUEUED"
    await db.commit()
    await service.enqueue_mission(build_queue(), mission)
    return {
        "experiment_id": exp.id,
        "mission_id": mission.id,
        "status": "QUEUED",
        "SIMULATED": True,
    }


@router.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    exp = await db.get(Experiment, experiment_id)
    if exp is None:
        raise not_found("EXPERIMENT_NOT_FOUND", f"No experiment {experiment_id}")
    runs = (
        (await db.execute(select(ExperimentRun).where(ExperimentRun.experiment_id == exp.id).limit(500)))
        .scalars()
        .all()
    )
    return {
        "id": exp.id,
        "hypothesis": exp.hypothesis,
        "status": exp.status,
        "cohort_size": exp.cohort_size,
        "is_simulated": True,
        "mission_id": exp.mission_id,
        "result_json": {**exp.result_json, "SIMULATED": True},
        "runs_summary": {
            "control": sum(1 for r in runs if r.arm == "control"),
            "treatment": sum(1 for r in runs if r.arm == "treatment"),
            "control_selected": sum(1 for r in runs if r.arm == "control" and r.selected),
            "treatment_selected": sum(1 for r in runs if r.arm == "treatment" and r.selected),
        },
    }


# --- Intel drill-downs --------------------------------------------------------


@router.get("/missions/{mission_id}/runs")
async def get_mission_runs(mission_id: str, db: AsyncSession = Depends(get_async_db)) -> list[dict]:
    from app.db.models import AgentRun

    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    rows = (
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.mission_id == mission_id)
                .order_by(AgentRun.created_at)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "agent_key": r.agent_key,
            "depth": r.depth,
            "parent_run_id": r.parent_run_id,
            "status": r.status,
            "objective": r.objective,
            "summary": r.summary,
            "latency_ms": r.latency_ms,
            "tool_calls_used": r.tool_calls_used,
            "confidence": r.confidence,
        }
        for r in rows
    ]


@router.get("/missions/{mission_id}/intel")
async def get_mission_intel(mission_id: str, db: AsyncSession = Depends(get_async_db)) -> dict:
    """Findings + recommendations for one mission (evidence-linked)."""
    from app.db.models import Evidence, Finding, Recommendation

    mission = await service.get_mission_or_none(db, mission_id)
    if mission is None:
        raise not_found("MISSION_NOT_FOUND", f"No mission {mission_id}")
    findings_rows = (
        (await db.execute(select(Finding).where(Finding.mission_id == mission_id).limit(100))).scalars().all()
    )
    rec_rows = (
        (await db.execute(select(Recommendation).where(Recommendation.mission_id == mission_id).limit(50)))
        .scalars()
        .all()
    )
    evidence_ids = {
        e.id: e
        for e in (
            (await db.execute(select(Evidence).where(Evidence.mission_id == mission_id).limit(500)))
            .scalars()
            .all()
        )
    }
    return {
        "findings": [
            {
                "id": f.id,
                "title": f.title,
                "statement": f.statement,
                "severity": f.severity,
                "confidence": f.confidence,
                "evidence_ids_json": f.evidence_ids_json,
                "evidence": [
                    _evidence_brief(evidence_ids[eid])
                    for eid in f.evidence_ids_json[:6]
                    if eid in evidence_ids
                ],
            }
            for f in findings_rows
        ],
        "recommendations": [
            {
                "id": r.id,
                "problem": r.problem,
                "why_it_matters": r.why_it_matters,
                "recommendation_text": r.recommendation_text,
                "expected_impact": r.expected_impact,
                "confidence": r.confidence,
                "impact": r.impact,
                "is_hypothesis": r.is_hypothesis,
                "priority_rank": r.priority_rank,
                "suggested_next_mission_json": r.suggested_next_mission_json or {},
            }
            for r in rec_rows
        ],
    }


def _evidence_brief(e) -> dict:
    return {
        "id": e.id,
        "claim": e.claim,
        "source_url": e.source_url,
        "source_type": e.source_type,
        "epistemic_state": e.epistemic_state,
        "excerpt": (e.excerpt or "")[:300],
        "observed_at": e.observed_at.isoformat() if e.observed_at else None,
    }


@router.get("/merchants/{merchant_id}/recommendations")
async def get_merchant_recommendations(
    merchant_id: str,
    limit: int = 20,
    status: str | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> list[dict]:
    from app.db.models import Recommendation

    limit = max(1, min(limit, 100))
    stmt = select(Recommendation).where(Recommendation.merchant_id == merchant_id)
    if status:
        stmt = stmt.where(Recommendation.status == status)
    rows = (await db.execute(stmt.order_by(Recommendation.created_at.desc()).limit(limit))).scalars().all()
    return [
        {
            "id": r.id,
            "problem": r.problem,
            "why_it_matters": r.why_it_matters,
            "recommendation_text": r.recommendation_text,
            "expected_impact": r.expected_impact,
            "confidence": r.confidence,
            "impact": r.impact,
            "is_hypothesis": r.is_hypothesis,
            "priority_rank": r.priority_rank,
            "status": r.status,
            "mission_id": r.mission_id,
            "suggested_next_mission_json": r.suggested_next_mission_json or {},
        }
        for r in rows
    ]


@router.get("/merchants/{merchant_id}/experiments")
async def get_merchant_experiments(merchant_id: str, db: AsyncSession = Depends(get_async_db)) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Experiment)
                .where(Experiment.merchant_id == merchant_id)
                .order_by(Experiment.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for exp in rows:
        runs = (
            (await db.execute(select(ExperimentRun).where(ExperimentRun.experiment_id == exp.id).limit(200)))
            .scalars()
            .all()
        )
        out.append(_experiment_payload(exp, runs))
    return out


def _experiment_payload(exp, runs) -> dict:
    return {
        "id": exp.id,
        "hypothesis": exp.hypothesis,
        "status": exp.status,
        "cohort_size": exp.cohort_size,
        "is_simulated": True,
        "mission_id": exp.mission_id,
        "result_json": {**(exp.result_json or {}), "SIMULATED": True} if exp.result_json else {},
        "runs_summary": {
            "control": sum(1 for r in runs if r.arm == "control"),
            "treatment": sum(1 for r in runs if r.arm == "treatment"),
            "control_selected": sum(1 for r in runs if r.arm == "control" and r.selected),
            "treatment_selected": sum(1 for r in runs if r.arm == "treatment" and r.selected),
        },
    }


# --- Meta --------------------------------------------------------------------


@router.get("/meta")
async def meta() -> dict[str, Any]:
    settings = get_settings()
    return {
        "queue_driver": "redis" if settings.redis_url else "in-process",
        "llm_configured": settings.llm_configured,
        "composio_ready": settings.composio_ready,
        "mem0_ready": settings.mem0_ready,
        "registered_mission_types": registered_types(),
        # Fleet PRD A4: public UI base for "inspect trace" deep links (no secret).
        "langfuse_ui": settings.langfuse_base_url if settings.langfuse_ready else None,
        "limits": {
            "max_concurrent_missions_global": settings.max_concurrent_missions_global,
            "max_concurrent_missions_per_merchant": settings.max_concurrent_missions_per_merchant,
            "max_agent_runs_per_mission": settings.max_agent_runs_per_mission,
            "max_sub_agent_depth": settings.max_sub_agent_depth,
        },
    }
