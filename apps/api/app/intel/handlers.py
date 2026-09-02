"""Mission handlers: on-demand specialist dispatch + Day-0 baseline graph.

Test seams: _get_llm / _get_memory / _get_plane / _session_factory are tiny
accessors so tests can monkeypatch fakes without touching call sites.

Budgets (PRD_3 §10/§36): every agent run consumes one unit of the mission's
budget_runs; every tool call consumes the run's budget_tool_calls. Exhaustion
ends the loop gracefully and the mission finishes with what it has.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.agents.base import RunContext
from app.core.config import get_settings
from app.db.models import (
    AgentRun,
    BaselineSnapshot,
    Finding,
    Merchant,
    MerchantProfile,
    Mission,
    UsageEvent,
)
from app.db.session import AsyncSessionLocal
from app.engine.context import BudgetExhausted, MissionContext, RunBudget
from app.engine.progress import ProgressEvent, progress_bus
from app.engine.state import MISSION_COMPLETED, MISSION_PARTIALLY_COMPLETED
from app.intel.agents_def import REGISTRY
from app.intel.identity import resolve_merchant_identity
from app.intel.persist import (
    persist_research_result,
    persist_strategy_result,
    record_run_usage,
)
from app.intel.schemas import (
    StrategySynthesisOutput,
)
from app.tools.router import ToolRouter, build_plane

logger = logging.getLogger("acg.intel.handlers")

# --- Test seams -------------------------------------------------------------


def _session_factory():
    """Return one fresh AsyncSession (supports `async with` at call sites)."""
    return AsyncSessionLocal()


def _get_llm():
    from app.llm.factory import get_llm_provider

    return get_llm_provider()


def _get_memory():
    from app.memory.factory import get_memory_store

    return get_memory_store()


def _get_plane():
    return build_plane()


# --- Core single-agent execution -------------------------------------------


async def execute_agent_run(
    *,
    mission_id: str,
    merchant_id: str,
    agent_key: str,
    objective: str,
    depth: int = 0,
    parent_run_id: str | None = None,
    extra: dict | None = None,
    findings_block: str | None = None,
    evidence_note: str | None = None,
) -> dict:
    """One bounded agent run end-to-end. Never raises; returns a summary dict."""
    settings = get_settings()
    started_wall = time.monotonic()

    async with _session_factory() as db:
        # Atomic budget claim BEFORE creating the run row (PRD_3 §23.4):
        # concurrent fan-out can never exceed the mission's budget_runs.
        if not await _claim_budget(db, mission_id):
            return {"ok": False, "error": "budget exhausted"}

        run = AgentRun(
            mission_id=mission_id,
            merchant_id=merchant_id,
            agent_key=agent_key,
            parent_run_id=parent_run_id,
            depth=depth,
            objective=objective[:2000],
            status="PENDING",
            budget_tool_calls=max(settings.max_tool_calls_per_run // (depth + 1), 4),
            timeout_seconds=(
                settings.buyer_run_timeout_seconds
                if agent_key == "buyer"
                else settings.agent_run_timeout_seconds
            ),
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        async def _publish(kind: str, **payload):
            await progress_bus().publish(
                ProgressEvent(
                    mission_id=mission_id,
                    kind=kind,
                    payload={"run_id": run_id, "agent": agent_key, **payload},
                )
            )

        async def _finish(status: str, **fields):
            run.status = status
            run.completed_at = datetime.now(UTC)
            run.latency_ms = int((time.monotonic() - started_wall) * 1000)
            for key, value in fields.items():
                setattr(run, key, value)
            await record_run_usage(db, merchant_id=merchant_id, mission_id=mission_id, run=run)
            await db.commit()
            await _publish("run_status", status=status)

        try:
            agent = REGISTRY[agent_key]
        except KeyError:
            await _finish("FAILED", error_category="unknown_agent", error_text=f"no agent {agent_key}")
            return {"ok": False, "error": "unknown agent"}

        merchant = await db.get(Merchant, merchant_id)
        profile = await db.scalar(select(MerchantProfile).where(MerchantProfile.merchant_id == merchant_id))
        profile_extra = getattr(profile, "extra_json", None) if profile else None
        identity_packet = None
        if isinstance(profile_extra, dict) and isinstance(profile_extra.get("identity_packet"), dict):
            identity_packet = profile_extra["identity_packet"]
        merchant_context = {
            "name": merchant.name if merchant else "unknown",
            "website_url": merchant.website_url if merchant else "",
            "category": getattr(profile, "primary_category", None),
            "description": getattr(profile, "business_description", None),
            "goal_text": getattr(profile, "goal_text", None),
            "competitors": [
                c.get("name") for c in (getattr(profile, "competitors_json", []) or []) if isinstance(c, dict)
            ],
            "baseline_summary": (getattr(profile, "reputation_json", {}) or {}).get("baseline_summary"),
            # Shared grounding context for every specialist (FIX_PRD_1 §9).
            "identity": identity_packet or {},
        }

        run.status = "RUNNING"
        run.started_at = datetime.now(UTC)
        await db.commit()

        router = ToolRouter(
            _get_plane(),
            agent_key=agent_key,
            mission_id=mission_id,
            budget=RunBudget(max_tool_calls=run.budget_tool_calls),
        )
        run_ctx = RunContext(
            mission_id=mission_id,
            run_id=run_id,
            merchant_id=merchant_id,
            agent_key=agent_key,
            objective=objective,
            depth=depth,
            parent_run_id=parent_run_id,
            contract=agent.contract,
            budget_tool_calls=run.budget_tool_calls,
            deadline_seconds=float(run.timeout_seconds),
            memory=_get_memory(),
            tools=router,
            llm=_get_llm(),
            merchant_context=merchant_context,
            extra={**(extra or {}), "findings_block": findings_block, "evidence_note": evidence_note},
        )

        try:
            # Agent-lifecycle observation (PRD 9.2): nests under the mission span.
            from app.observability import observation, propagate_attributes

            propagate_attributes(
                mission_id=mission_id,
                run_id=run_id,
                merchant_id=merchant_id,
                agent_id=run_id,
                agent_type=agent_key,
                depth=depth,
            )
            with observation(
                name=f"agent.{agent_key}",
                as_type="agent",
                input={"objective": (objective or "")[:400], "depth": depth},
            ) as _agent_span:
                result, raw = await asyncio.wait_for(
                    agent.execute(run_ctx), timeout=float(run.timeout_seconds)
                )
                if _agent_span is not None:
                    try:
                        _agent_span.update(status="COMPLETED")
                    except Exception:
                        pass
            run.tool_calls_used = run_ctx.tools.budget.tool_calls_used
        except asyncio.TimeoutError:
            await _finish("TIMED_OUT", error_category="timeout", error_text="run deadline exceeded")
            return {"ok": False, "error": "timeout"}
        except BudgetExhausted as exc:
            await _finish("COMPLETED", summary=f"stopped early: {exc}")
            return {"ok": True, "partial": True}
        except Exception as exc:
            logger.exception("agent %s failed", agent_key)
            await _finish("FAILED", error_category="exception", error_text=str(exc)[:400])
            return {"ok": False, "error": str(exc)[:200]}

        # Persist raw tool observations as evidence (provenance, PRD_3 §16).
        from app.intel.persist import persist_observations

        obs_ids = await persist_observations(
            db,
            merchant_id=merchant_id,
            mission_id=mission_id,
            run_id=run_id,
            observations=list(router.observations),
            identity=identity_packet,
        )

        # Persist typed outputs by result type.
        finding_ids: list[str] = []
        finding_title_map: dict[str, str] = {}
        rec_ids: list[str] = []
        relevance_stats: dict = {}

        if isinstance(result, StrategySynthesisOutput):
            existing_findings = (
                (await db.execute(select(Finding).where(Finding.mission_id == mission_id).limit(200)))
                .scalars()
                .all()
            )
            finding_title_map = {f.title: f.id for f in existing_findings}
            rec_ids = await persist_strategy_result(
                db,
                merchant_id=merchant_id,
                mission_id=mission_id,
                run_id=run_id,
                result=result,
                finding_title_to_ids=finding_title_map,
            )
            result_json = {
                "recommendations": [
                    {
                        "problem": r.problem,
                        "recommendation": r.recommendation_text,
                        "impact": r.impact,
                        "suggested_next_mission": r.suggested_next_mission,
                    }
                    for r in result.recommendations
                ],
                "conflicting_signals": result.conflicting_signals,
                "llm_model": raw.model_used if raw else None,
                "observation_ids": len(obs_ids),
            }
        else:
            finding_ids, relevance_stats = await persist_research_result(
                db,
                merchant_id=merchant_id,
                mission_id=mission_id,
                run_id=run_id,
                agent_key=agent_key,
                result=result,
            )
            for row in await db.scalars(select(Finding).where(Finding.id.in_(finding_ids))):
                finding_title_map[row.title] = row.id
            result_json = {
                "selected": getattr(result, "selected", None),
                "findings": [f.title for f in result.findings][:10],
                "claims": len(getattr(result, "claims", [])),
                "llm_model": raw.model_used if raw else None,
                "observation_ids": len(obs_ids),
                "relevance": {k: v for k, v in relevance_stats.items() if k != "rejected_claims"},
            }
            # Fleet PRD B3: the factual transactable outcome is gateway truth,
            # not LLM prose — persist it so "why didn't checkout happen" is
            # answerable from Postgres alone (outcome, blocked reason codes,
            # untransactable reasons, session/txn refs).
            transaction = getattr(result, "transaction", None)
            if isinstance(transaction, dict) and transaction.get("sessions"):
                result_json["transaction"] = transaction["sessions"]

        await _finish(
            "COMPLETED", summary=result.summary[:1000], confidence=result.confidence, result_json=result_json
        )
        return {
            "ok": True,
            "summary": result.summary,
            "finding_ids": finding_ids,
            "finding_titles": list(finding_title_map),
            "recommendation_ids": rec_ids,
            "result": result,
            "relevance_stats": relevance_stats,
            "observation_count": len(obs_ids),
        }


async def _claim_budget(db, mission_id: str) -> bool:
    """Atomically consume one unit of mission budget (race-free, §23.4)."""
    result = await db.execute(
        update(Mission)
        .where(Mission.id == mission_id, Mission.runs_used < Mission.budget_runs)
        .values(runs_used=Mission.runs_used + 1)
    )
    await db.commit()
    return bool(result.rowcount)


async def _remaining_budget(db, mission_id: str) -> int:
    from app.db.models import Mission

    mission = await db.get(Mission, mission_id)
    if mission is None:
        return 0
    return max(0, (mission.budget_runs or 0) - int(mission.runs_used or 0))


# --- Identity resolution (FIX_PRD_1 §5-§9) ------------------------------------


def _identity_summary_block(identity: dict, degraded: bool, meta: dict) -> dict:
    return {
        "canonical_name": identity.get("canonical_name"),
        "domain": identity.get("domain"),
        "primary_category": identity.get("primary_category"),
        "geography": identity.get("geography"),
        "identity_confidence": identity.get("identity_confidence"),
        "degraded": degraded,
        "ambiguity_notes": list(meta.get("ambiguity_notes") or []),
    }


async def _load_identity(merchant_id: str) -> dict | None:
    """Read the stored identity packet from the merchant profile (additive JSON)."""
    async with _session_factory() as db:
        profile = await db.scalar(
            select(MerchantProfile).where(MerchantProfile.merchant_id == merchant_id)
        )
        extra = getattr(profile, "extra_json", None) if profile else None
        if isinstance(extra, dict) and isinstance(extra.get("identity_packet"), dict):
            return extra["identity_packet"]
    return None


async def _store_identity(merchant_id: str, packet_dict: dict, degraded: bool) -> None:
    """Persist the identity packet on the merchant profile (no schema migration).

    Memory interaction (FIX_PRD_1 §26): only high-confidence identity facts are
    written to semantic memory, and re-resolution always rebuilds from
    first-party evidence first — current first-party truth beats stale memory.
    """
    async with _session_factory() as db:
        profile = await db.scalar(
            select(MerchantProfile).where(MerchantProfile.merchant_id == merchant_id)
        )
        if profile is None:
            profile = MerchantProfile(merchant_id=merchant_id, version=0)
            db.add(profile)
        extra = dict(getattr(profile, "extra_json", None) or {})
        extra["identity_packet"] = packet_dict
        profile.extra_json = extra
        if getattr(profile, "primary_category", None) is None and packet_dict.get("primary_category"):
            profile.primary_category = packet_dict["primary_category"]
        await db.commit()
    if degraded:
        return
    try:
        await _get_memory().add(
            merchant_id,
            (
                f"Verified merchant identity: {packet_dict.get('canonical_name')} "
                f"({packet_dict.get('domain')}), "
                f"{packet_dict.get('primary_category') or 'category unknown'}, "
                f"{packet_dict.get('geography') or 'geography unknown'}."
            ),
            kind="fact",
            metadata={"source": "identity_resolution"},
        )
    except Exception:
        logger.info("identity memory write skipped", exc_info=True)


async def _resolve_or_load_identity(
    *, mission_id: str, merchant_id: str, objective: str
) -> tuple[dict, bool, dict]:
    """Return (identity_packet_dict, degraded, meta); resolve once per merchant."""
    settings = get_settings()
    existing = await _load_identity(merchant_id)
    if existing:
        confidence = float(existing.get("identity_confidence") or 0.0)
        return existing, confidence < settings.identity_confidence_threshold, {}
    async with _session_factory() as db:
        merchant = await db.get(Merchant, merchant_id)
    url = (getattr(merchant, "website_url", None) or "") if merchant else ""
    name = (getattr(merchant, "name", None) or "") if merchant else ""
    if not url:
        return {}, True, {}
    try:
        packet, observations, meta = await asyncio.wait_for(
            resolve_merchant_identity(
                mission_id=mission_id,
                merchant_id=merchant_id,
                url=url,
                merchant_name=name,
                goal=objective[:400],
            ),
            timeout=float(settings.identity_timeout_seconds * 2),
        )
    except Exception as exc:
        logger.warning("identity resolution failed for %s: %s", merchant_id, exc)
        return {}, True, {}
    packet_dict = packet.model_dump()
    degraded = packet.identity_confidence < settings.identity_confidence_threshold
    await _store_identity(merchant_id, packet_dict, degraded)
    # Identity observations become auditable evidence (no run row needed).
    from app.intel.persist import persist_observations

    async with _session_factory() as db:
        await persist_observations(
            db,
            merchant_id=merchant_id,
            mission_id=mission_id,
            run_id=None,
            observations=observations,
            identity=packet_dict,
        )
    return packet_dict, degraded, meta


def _aggregate_quality(phase_results: dict, synthesis: dict) -> dict:
    """Research-quality diagnostics (FIX_PRD_1 §22) — counts, not scores."""
    keys = ("claims_total", "claims_rejected", "claims_unscored", "unsupported_claims", "findings_dropped")
    totals: dict = {k: 0 for k in keys}
    sources_discovered = 0
    for res in [*phase_results.values(), synthesis]:
        if not isinstance(res, dict):
            continue
        stats = res.get("relevance_stats") or {}
        for k in keys:
            totals[k] += int(stats.get(k) or 0)
        sources_discovered += int(res.get("observation_count") or 0)
    accepted = totals["claims_total"] - totals["claims_rejected"]
    totals["sources_discovered"] = sources_discovered
    totals["claims_accepted"] = accepted
    totals["entity_relevance_rate"] = (
        round(accepted / totals["claims_total"], 3) if totals["claims_total"] else None
    )
    return totals


# --- Mission handlers --------------------------------------------------------


async def _findings_block_for(mission_id: str) -> tuple[str, str]:
    """Render findings + evidence note for strategy synthesis."""
    async with _session_factory() as db:
        rows = (
            (await db.execute(select(Finding).where(Finding.mission_id == mission_id).limit(100)))
            .scalars()
            .all()
        )
    lines = []
    evidence_note_parts = []
    for f in rows:
        lines.append(f"- [{f.severity}|conf={f.confidence}] {f.title}: {f.statement[:220]}")
        if f.evidence_ids_json:
            evidence_note_parts.append(f"{f.title}: {len(f.evidence_ids_json)} evidence item(s)")
    return "\n".join(lines), "; ".join(evidence_note_parts)


async def _dispatch_specialists(
    ctx: MissionContext, agent_keys: list[str], *, personas: bool = False
) -> dict:
    """Fan-out specialists in parallel under bounded concurrency (PRD_3 §22/§23.3)."""
    sem = asyncio.Semaphore(min(len(agent_keys), get_settings().max_concurrent_missions_global + 2))
    results: dict[str, dict] = {}

    async def _one(key: str) -> None:
        async with sem:
            objective = ctx.objective
            extra = {}
            if personas and key == "buyer":
                extra["persona"] = (
                    "A skeptical beginner buyer on a tight budget who abandons "
                    "carts when delivery dates or returns are unclear."
                )
            results[key] = await execute_agent_run(
                mission_id=ctx.mission_id,
                merchant_id=ctx.merchant_id,
                agent_key=key,
                objective=objective,
                extra=extra,
            )

    await ctx.ensure_not_cancelled()
    await asyncio.gather(*(_one(k) for k in agent_keys))
    return results


async def _synthesize(ctx: MissionContext) -> dict:
    findings_block, evidence_note = await _findings_block_for(ctx.mission_id)
    return await execute_agent_run(
        mission_id=ctx.mission_id,
        merchant_id=ctx.merchant_id,
        agent_key="strategy",
        objective=ctx.objective,
        findings_block=findings_block or None,
        evidence_note=evidence_note or None,
    )


async def _on_demand_handler(ctx: MissionContext) -> dict:
    """Route an on-demand/recurring mission to its assigned specialists."""
    settings = get_settings()
    identity, identity_degraded, identity_meta = await _resolve_or_load_identity(
        mission_id=ctx.mission_id, merchant_id=ctx.merchant_id, objective=ctx.objective
    )
    assigned = [k for k in (ctx.artifacts.get("agent_assignments") or []) if k in REGISTRY]
    if not assigned:
        assigned = ["market", "competitor", "presence", "reviews"]  # sensible research default
    assigned = assigned[: max(settings.max_children_per_parent, 6)]

    specialist_results = await _dispatch_specialists(ctx, assigned)
    await ctx.ensure_not_cancelled()
    synthesis = await _synthesize(ctx)

    ok_count = sum(1 for r in specialist_results.values() if r.get("ok"))
    status = MISSION_COMPLETED if ok_count == len(specialist_results) else MISSION_PARTIALLY_COMPLETED
    return {
        "_status": status,
        "specialists": {
            k: {"ok": v.get("ok"), "error": v.get("error")} for k, v in specialist_results.items()
        },
        "strategy_ok": synthesis.get("ok", False),
        "recommendation_ids": synthesis.get("recommendation_ids", []),
        "identity": _identity_summary_block(identity, identity_degraded, identity_meta),
        "quality": _aggregate_quality(specialist_results, synthesis),
        "summary": f"{ok_count}/{len(specialist_results)} specialists completed; strategy synthesized.",
    }


async def _buyer_sim_handler(ctx: MissionContext) -> dict:
    """Fast buyer-only simulation loop (dashboard "Simulate buyers" action).

    Skips the research fleet AND strategy synthesis: identity loads from the
    cached packet, then ONE buyer run executes the full transactable attempt
    (memory -> live-web materialization -> quote -> cart -> checkout -> policy
    -> payment). Exists so buyer behavior can be iterated without re-running
    the whole pipeline.
    """
    identity, identity_degraded, identity_meta = await _resolve_or_load_identity(
        mission_id=ctx.mission_id, merchant_id=ctx.merchant_id, objective=ctx.objective
    )
    await ctx.ensure_not_cancelled()
    run = await execute_agent_run(
        mission_id=ctx.mission_id,
        merchant_id=ctx.merchant_id,
        agent_key="buyer",
        objective=ctx.objective,
        depth=0,
    )
    ok = bool(run.get("ok"))
    return {
        "_status": MISSION_COMPLETED if ok else MISSION_PARTIALLY_COMPLETED,
        "specialists": {"buyer": {"ok": ok, "error": run.get("error")}},
        "identity": _identity_summary_block(identity, identity_degraded, identity_meta),
        "summary": (
            "Buyer simulation completed."
            if ok
            else f"Buyer simulation failed: {str(run.get('error') or 'unknown')[:200]}"
        ),
    }


def _extract_shopping_spec(objective: str) -> dict:
    """Pull a structured shopping block from the objective (B7).

    The route injects it as a leading line: ``SHOPPING_SPEC {...json...}``.
    Falls back to {} if absent/unparsable so text-only objectives keep working.
    """
    import json as _json

    objective = (objective or "").lstrip()
    if not objective.startswith("SHOPPING_SPEC "):
        return {}
    first_line = objective.splitlines()[0]
    try:
        spec = _json.loads(first_line[len("SHOPPING_SPEC ") :])
    except _json.JSONDecodeError:
        return {}
    return spec if isinstance(spec, dict) else {}


def _parse_budget_minor(raw: object) -> int | None:
    """Parse a human budget string ('5,000', '5000', '5k') into paise, or None.

    Guards the buyer authorization so the agent can never invent an amount.
    """
    if raw is None:
        return None
    text = str(raw).strip().lower().replace(",", "")
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return int(round(amount * multiplier * 100))  # to paise


async def _shopping_handler(ctx: MissionContext) -> dict:
    """Direct shopping mission (B7): parse the user's product spec + budget and
    drive ONE buyer run that materializes via the managed stealth browser and
    moves straight toward checkout. No research fleet, no strategy pass — the
    user already knows exactly what they want to buy.
    """
    spec = _extract_shopping_spec(ctx.objective)
    base_objective = (
        ctx.objective.lstrip().split("\n", 1)[1]
        if ctx.objective.lstrip().startswith("SHOPPING_SPEC ")
        else ctx.objective
    )
    objective = base_objective

    # Parse a human budget if given (INR), else leave the gateway default.
    budget_minor = _parse_budget_minor(spec.get("budget"))
    if budget_minor is None and spec.get("budget_minor") is not None:
        try:
            budget_minor = int(spec["budget_minor"])
        except (TypeError, ValueError):
            budget_minor = None

    # Preference summary for the intent string.
    pref_bits = []
    if spec.get("product"):
        pref_bits.append(f"product: {spec['product']}")
    if spec.get("size"):
        pref_bits.append(f"size: {spec['size']}")
    if spec.get("color"):
        pref_bits.append(f"color: {spec['color']}")
    if spec.get("brand"):
        pref_bits.append(f"brand: {spec['brand']}")
    for extra_key in ("gender", "category", "notes"):
        if spec.get(extra_key):
            pref_bits.append(f"{extra_key}: {spec[extra_key]}")

    objective = (
        f"{objective}\n\nSHOPPER PREFERENCES\n" + ("\n".join(pref_bits) or "(none given)")
    )

    identity, identity_degraded, identity_meta = await _resolve_or_load_identity(
        mission_id=ctx.mission_id, merchant_id=ctx.merchant_id, objective=objective
    )
    await ctx.ensure_not_cancelled()

    run = await execute_agent_run(
        mission_id=ctx.mission_id,
        merchant_id=ctx.merchant_id,
        agent_key="buyer",
        objective=objective,
        depth=0,
        extra={
            "shopping": {
                **spec,
                "budget_minor": budget_minor,
                "force_browser": True,
            },
            "buyer_mission": objective,
            "persona": "A decisive shopper who knows exactly what they want, "
            "wants the exact specified product/size/color within budget.",
        },
    )
    ok = bool(run.get("ok"))
    return {
        "_status": MISSION_COMPLETED if ok else MISSION_PARTIALLY_COMPLETED,
        "specialists": {"buyer": {"ok": ok, "error": run.get("error")}},
        "identity": _identity_summary_block(identity, identity_degraded, identity_meta),
        "summary": (
            "Shopping attempt completed."
            if ok
            else f"Shopping attempt failed: {str(run.get('error') or 'unknown')[:200]}"
        ),
    }


async def _baseline_handler(ctx: MissionContext) -> dict:
    """Day-0 diagnostic graph (PRD_3 §6):

    Phase A (parallel): market business research | competitor discovery |
                        presence scan
    Phase B:            AI buyer simulations (persona fan-out, depth 1)
    Phase C:            initial strategy synthesis -> BaselineSnapshot vN
    """
    settings = get_settings()
    identity, identity_degraded, identity_meta = await _resolve_or_load_identity(
        mission_id=ctx.mission_id, merchant_id=ctx.merchant_id, objective=ctx.objective
    )
    phase_results: dict[str, dict] = {}

    research_agents = ["market", "competitor", "presence", "reviews", "ads", "catalog"]
    sem = asyncio.Semaphore(3)
    tasks = {}

    async def _one(key: str, persona: str | None):
        async with sem:
            await ctx.ensure_not_cancelled()
            extra = {}
            objective = ctx.objective
            if key == "buyer":
                extra["persona"] = persona
                objective = f"Baseline buyer simulation for {ctx.objective[:120]}"
                extra["buyer_mission"] = (
                    f"Find and choose a product matching this store's main category "
                    f"as if you were a first-time online buyer. Store: "
                    f"{ctx.merchant_id}. Consider alternatives before deciding."
                )
            phase_results[f"{key}:{persona or ''}"] = await execute_agent_run(
                mission_id=ctx.mission_id,
                merchant_id=ctx.merchant_id,
                agent_key=key,
                objective=objective,
                depth=1 if persona else 0,
                extra=extra,
            )

    for key in research_agents:
        tasks[key + ":"] = _one(key, None)

    # Buyer persona fan-out (PRD_3 §11 star pattern; children never spawn).
    personas = [
        "Price-conscious beginner; needs clear pricing and free/cheap returns.",
        "Quality-first buyer; reads reviews carefully, tolerates higher price.",
    ][: max(settings.max_children_per_parent - len(research_agents), 2)]

    done = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for t, res in zip(tasks, done, strict=True):
        if isinstance(res, Exception):
            logger.warning("baseline phase A %s failed: %s", t, res)

    async with _session_factory() as db:
        budget_left = await _remaining_budget(db, ctx.mission_id)
    if budget_left <= 0:
        version = await _write_baseline_snapshot(ctx.mission_id, ctx.merchant_id, phase_results, {})
        return {
            "_status": MISSION_PARTIALLY_COMPLETED,
            "snapshot_version": version,
            "phases": {k: {"ok": v.get("ok")} for k, v in phase_results.items()},
            "strategy_ok": False,
            "identity": _identity_summary_block(identity, identity_degraded, identity_meta),
            "quality": _aggregate_quality(phase_results, {}),
            "summary": "Budget exhausted after research phases; snapshot from partial results.",
        }

    await ctx.ensure_not_cancelled()
    buyer_parent = await execute_agent_run(
        mission_id=ctx.mission_id,
        merchant_id=ctx.merchant_id,
        agent_key="buyer",
        objective=f"Baseline buyer simulations ({len(personas)} personas)",
        extra={
            "personas": personas,
            "buyer_mission": "Complete a typical purchase in this merchant's category.",
        },
    )
    phase_results["buyer"] = buyer_parent

    synthesis = {}
    if await ctx.cancelled():
        raise asyncio.CancelledError()
    try:
        synthesis = await _synthesize(ctx)
    except Exception as exc:
        logger.exception("baseline synthesis failed: %s", exc)

    snapshot_version = await _write_baseline_snapshot(
        ctx.mission_id, ctx.merchant_id, phase_results, synthesis
    )

    ok_count = sum(1 for r in phase_results.values() if r.get("ok"))
    total = len(phase_results) + 1
    score = round(ok_count / max(total, 1), 2)
    return {
        "_status": MISSION_COMPLETED if ok_count >= total - 1 else MISSION_PARTIALLY_COMPLETED,
        "snapshot_version": snapshot_version,
        "phases": {k: {"ok": v.get("ok")} for k, v in phase_results.items()},
        "strategy_ok": synthesis.get("ok", False),
        "recommendation_ids": synthesis.get("recommendation_ids", []),
        "identity": _identity_summary_block(identity, identity_degraded, identity_meta),
        "quality": _aggregate_quality(phase_results, synthesis),
        "health_score": score,
        "summary": (
            f"Baseline complete: {ok_count}/{total} phases succeeded; snapshot v{snapshot_version} written."
            + (" Identity degraded; queries domain-anchored." if identity_degraded else "")
        ),
    }


async def _write_baseline_snapshot(
    mission_id: str, merchant_id: str, phase_results: dict, synthesis: dict
) -> int:
    async with _session_factory() as db:
        max_version = await db.scalar(
            select(func.max(BaselineSnapshot.version)).where(BaselineSnapshot.merchant_id == merchant_id)
        )
        version = int(max_version or 0) + 1
        snapshot = BaselineSnapshot(
            merchant_id=merchant_id,
            version=version,
            mission_id=mission_id,
            status="complete",
            snapshot_json={
                "generated_at": datetime.now(UTC).isoformat(),
                "phases": {
                    k: {"ok": v.get("ok"), "summary": (v.get("summary") or "")[:300]}
                    for k, v in phase_results.items()
                },
                "strategy": {
                    "ok": synthesis.get("ok"),
                    "recommendation_ids": synthesis.get("recommendation_ids", []),
                },
            },
        )
        db.add(snapshot)
        await db.commit()
        return version


def register_all() -> None:
    from app.engine.context import register_handler

    register_handler("on_demand", _on_demand_handler)
    register_handler("recurring", _on_demand_handler)
    register_handler("buyer_sim", _buyer_sim_handler)
    register_handler("shopping", _shopping_handler)
    register_handler("baseline", _baseline_handler)
