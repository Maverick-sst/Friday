"""Persistence of agent-run results: Evidence -> Findings -> Recommendations.

Every finding cites evidence ids; every recommendation cites findings. This
module is the only writer of intel tables, so provenance rules (PRD_3 §16-§18)
are enforced in exactly one place.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AgentRun, Evidence, Finding, Recommendation, UsageEvent
from app.intel.identity import domain_of
from app.intel.schemas import BuyerSimulationOutput, ResearchOutput, StrategySynthesisOutput
from app.tools.base import ToolObservation

logger = logging.getLogger("acg.intel.persist")


def _source_class(url: str | None, identity: dict | None) -> str:
    """Coarse source classification for audit (FIX_PRD_1 §19)."""
    if not identity or not url:
        return "unclassified"
    official = {str(d).lower() for d in identity.get("official_domains", []) if d}
    if domain_of(url) in official:
        return "first_party"
    return "third_party"


async def persist_observations(
    db: AsyncSession,
    *,
    merchant_id: str,
    mission_id: str,
    run_id: str | None,
    observations: list[ToolObservation],
    identity: dict | None = None,
) -> list[str]:
    """Persist tool observations as evidence rows; returns evidence ids.

    Observations are the audit trail — nothing is rejected here (FIX_PRD_1
    §13); each row is classified first_party/third_party when an identity
    packet is supplied. Findings never cite these rows, so the strategy path
    is unaffected.
    """
    ids: list[str] = []
    for obs in observations:
        if obs.capability == "fetch_url":
            claim = f"Fetched page content from {obs.query_or_url}"
            url = obs.query_or_url
        else:
            claim = f"{obs.capability} for {obs.query_or_url!r}"
            url = obs.hits[0].url if obs.hits and obs.hits[0].url else None
            for h in obs.hits[:5]:
                if not h.url:
                    continue
                ev = Evidence(
                    merchant_id=merchant_id,
                    mission_id=mission_id,
                    agent_run_id=run_id,
                    source_url=h.url,
                    source_type=h.source or "web",
                    claim=claim,
                    excerpt=f"{h.title}: {h.snippet}"[:900],
                    epistemic_state="fact",
                    meta_json={"latency_ms": obs.latency_ms, "source_class": _source_class(h.url, identity)},
                )
                db.add(ev)
                await db.flush()
                ids.append(ev.id)
            continue
        if not obs.ok:
            claim += f" [FAILED: {obs.error}]"
        ev = Evidence(
            merchant_id=merchant_id,
            mission_id=mission_id,
            agent_run_id=run_id,
            source_url=url,
            source_type="page",
            claim=claim[:900],
            excerpt=(obs.text or "")[:900] if obs.text else None,
            epistemic_state="fact",
            meta_json={"latency_ms": obs.latency_ms, "source_class": _source_class(url, identity)},
        )
        db.add(ev)
        await db.flush()
        ids.append(ev.id)
    return ids


async def persist_research_result(
    db: AsyncSession,
    *,
    merchant_id: str,
    mission_id: str,
    run_id: str,
    agent_key: str,
    result: ResearchOutput | BuyerSimulationOutput,
) -> tuple[list[str], dict]:
    """Persist claims as evidence + findings citing them, behind the
    entity-relevance gate (FIX_PRD_1 §11-§13).

    Returns (finding_ids, relevance_stats). Claims scored below
    settings.evidence_relevance_threshold never become Evidence rows; findings
    whose every cited claim was rejected are dropped from the findings/strategy
    path (the raw LLM result stays on the run row for audit). Unscored claims
    (entity_relevance=None, legacy outputs) are promoted as before.
    """
    settings = get_settings()
    threshold = settings.evidence_relevance_threshold
    stats: dict = {
        "claims_total": 0,
        "claims_rejected": 0,
        "claims_unscored": 0,
        "unsupported_claims": 0,
        "findings_dropped": 0,
        "rejected_claims": [],
    }

    evidence_ids: list[str] = []
    evidence_by_index: dict[int, str] = {}  # original claim position -> evidence id
    for idx, claim in enumerate(getattr(result, "claims", [])):
        stats["claims_total"] += 1
        relevance = getattr(claim, "entity_relevance", None)
        if relevance is not None and relevance < threshold:
            # Factually true but about something else: not usable evidence.
            stats["claims_rejected"] += 1
            if len(stats["rejected_claims"]) < 10:
                stats["rejected_claims"].append(
                    {"claim": claim.claim[:200], "relevance": relevance, "source_url": claim.source_url}
                )
            continue
        if relevance is None:
            stats["claims_unscored"] += 1
        if not claim.source_url and (claim.confidence or 0.0) < 0.5:
            stats["unsupported_claims"] += 1
        ev = Evidence(
            merchant_id=merchant_id,
            mission_id=mission_id,
            agent_run_id=run_id,
            source_url=claim.source_url,
            source_type="research",
            claim=claim.claim[:900],
            excerpt=(claim.excerpt or None) and claim.excerpt[:900],
            observed_at=None,
            epistemic_state=claim.epistemic_state,
            confidence=claim.confidence,
            meta_json={"entity_relevance": relevance},
        )
        db.add(ev)
        await db.flush()
        evidence_ids.append(ev.id)
        evidence_by_index[idx] = ev.id

    finding_ids: list[str] = []
    for f in result.findings:
        cited = [evidence_by_index[i] for i in f.claim_indexes if i in evidence_by_index]
        if f.claim_indexes and not cited:
            # Every claim this finding cited was relevance-rejected.
            stats["findings_dropped"] += 1
            continue
        row = Finding(
            merchant_id=merchant_id,
            mission_id=mission_id,
            agent_run_id=run_id,
            agent_key=agent_key,
            title=f.title[:512],
            statement=f.statement,
            severity=f.severity,
            confidence=f.confidence,
            evidence_ids_json=cited,
            tags_json=f.tags,
        )
        db.add(row)
        await db.flush()
        finding_ids.append(row.id)
    return finding_ids, stats


async def persist_strategy_result(
    db: AsyncSession,
    *,
    merchant_id: str,
    mission_id: str,
    run_id: str,
    result: StrategySynthesisOutput,
    finding_title_to_ids: dict[str, str],
) -> list[str]:
    """Persist strategy recommendations; rank them; link findings by title."""
    rec_ids: list[str] = []
    for rank, rec in enumerate(result.recommendations, start=1):
        linked_ids = [
            finding_title_to_ids[t.strip()] for t in rec.finding_titles if t.strip() in finding_title_to_ids
        ]
        has_evidence = bool(linked_ids)
        row = Recommendation(
            merchant_id=merchant_id,
            mission_id=mission_id,
            problem=rec.problem,
            why_it_matters=rec.why_it_matters,
            recommendation_text=rec.recommendation_text,
            expected_impact=rec.expected_impact,
            confidence=rec.confidence,
            is_hypothesis=not has_evidence,  # PRD_3 §16: no-evidence recs are hypotheses
            finding_ids_json=linked_ids,
            suggested_next_mission_json=(
                {"objective": rec.suggested_next_mission} if rec.suggested_next_mission else {}
            ),
            priority_rank=rank,
            impact=rec.impact,
        )
        db.add(row)
        rec_ids.append(row.id)
    await db.flush()
    return rec_ids


async def record_run_usage(db: AsyncSession, *, merchant_id: str, mission_id: str, run: AgentRun) -> None:
    db.add(
        UsageEvent(
            merchant_id=merchant_id,
            mission_id=mission_id,
            agent_run_id=run.id,
            kind="agent_run",
            quantity=1,
            duration_ms=run.latency_ms,
            meta_json={"agent": run.agent_key, "status": run.status},
        )
    )
