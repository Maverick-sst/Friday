"""Transactable buyer bridge (Fleet PRD B3).

Connects the intel fleet's BuyerSimulationAgent to the REAL V0 gateway: an
agent session is created for the intel merchant, the standard runner drives
discover -> search_products -> get_quote -> create_cart -> checkout through
the deterministic policy engine, and the payment is auto-completed when the
rail is the mock provider (Razorpay test mode stays at PAYMENT_PENDING, since
hosted completion needs the real checkout).

Every runner event is republished onto the mission's progress bus, so the
purchase plays out live in the mission UI and inherits Langfuse tracing via
the buyer run's active span.
"""

import asyncio
import logging
import secrets

from app.agent import runner as agent_runner
from app.db.models import AgentSession, Merchant
from app.db.session import SessionLocal
from app.domain.contracts import StartAgentSessionRequest
from app.engine.progress import ProgressEvent, progress_bus

logger = logging.getLogger("acg.intel.transact")

# Buyer authorization default: ₹5,000 in minor units (PRD §13 example).
DEFAULT_BUDGET_MINOR = 500_000


def _outcome_from_events(events: list[dict]) -> dict:
    """Derive the structured TransactionOutcome from runner events."""
    outcome: dict = {"attempted": True, "outcome": "INCOMPLETE", "steps": len(events)}
    for ev in reversed(events):  # newest first
        if ev.get("type") == "final":
            payload = ev.get("payload") or {}
            raw = payload.get("outcome")
            outcome["outcome"] = {
                "BLOCKED": "BLOCKED",
                "PAYMENT_PENDING": "AUTHORIZED",
                "COMPLETED": "COMPLETED",
            }.get(raw, raw or "INCOMPLETE")
            outcome["selection"] = payload.get("selection")
            break
    for ev in reversed(events):
        if ev.get("type") == "tool_result" and ev.get("tool") == "checkout":
            result = (ev.get("payload") or {}).get("result") or {}
            if result.get("blocked"):
                outcome["outcome"] = "BLOCKED"
                outcome["blocked_reason_codes"] = result.get("reason_codes") or []
                outcome["explanation"] = next(
                    (
                        d.get("explanation")
                        for d in [result.get("decision") or {}]
                        if isinstance(d, dict)
                    ),
                    None,
                )
            else:
                initiation = result.get("payment_initiation") or {}
                outcome["txn_ref"] = result.get("transaction_id")
                outcome["payment_provider"] = initiation.get("provider")
                outcome["order_id"] = initiation.get("order_id")
                outcome["amount_minor"] = initiation.get("amount_minor")
                outcome["currency"] = initiation.get("currency")
                outcome["status"] = result.get("status")
            break
    return outcome


def _run_session_sync(session_id: str, events_out: list, loop, mission_id: str) -> None:
    """Consume the sync runner generator off the event loop (own DB session).

    Every AgentEvent is (a) appended to events_out and (b) republished onto the
    mission's progress bus via run_coroutine_threadsafe, preserving order.
    """
    from sqlalchemy import select

    bus = progress_bus()
    db = SessionLocal()
    try:
        session_row = db.scalar(select(AgentSession).where(AgentSession.session_id == session_id))
        merchant = db.scalar(select(Merchant).where(Merchant.id == session_row.merchant_id))
        for ev in agent_runner.run_agent_session(db, session_row, merchant):
            data = ev.model_dump(mode="json")
            events_out.append(data)
            kind = {
                "tool_call": "tool_call",
                "tool_result": "tool_call",
                "status": "log",
                "error": "log",
                "final": "run_status",
            }.get(data.get("type"), "log")
            payload = {
                "agent": "buyer",
                "session_id": session_id,
                "runner_event": data.get("type"),
                "label": (data.get("label") or "")[:160],
                "tool": data.get("tool"),
                "status": data.get("payload", {}).get("outcome") or data.get("payload", {}).get("status"),
                "target": (data.get("label") or "")[:120],
            }
            try:
                asyncio.run_coroutine_threadsafe(
                    bus.publish(
                        ProgressEvent(mission_id=mission_id, kind=kind, payload=payload)
                    ),
                    loop,
                ).result(timeout=10)
            except Exception as exc:  # streaming is best-effort
                logger.warning("transact event publish failed: %s", exc)
    finally:
        db.close()


def _start_session_sync(merchant_id: str, intent: str, constraints: dict) -> str:
    """Create the AgentSession row on a sync session; returns session_id."""
    from sqlalchemy import select

    from app.services.sessions import create_session

    db = SessionLocal()
    try:
        merchant = db.scalar(select(Merchant).where(Merchant.id == merchant_id))
        if merchant is None:
            raise LookupError(f"merchant {merchant_id} not found for transactable session")
        req = StartAgentSessionRequest(
            intent=intent,
            max_budget_minor=int(constraints.get("max_budget_minor") or DEFAULT_BUDGET_MINOR),
            currency=constraints.get("currency") or "INR",
            demo_scenario=constraints.get("demo_scenario"),
        )
        row = create_session(db, req, merchant.id)
        return row.session_id
    finally:
        db.close()


def _auto_complete_payment_sync(session_id: str, outcome: dict) -> dict:
    """Complete the mock-provider payment headlessly (hosted-checkout stand-in).

    Razorpay test mode intentionally stays PAYMENT_PENDING: signature
    verification requires the real hosted checkout. Outcome dict is updated
    in place and returned.
    """
    from app.adapters import payment_provider_name
    from app.services import checkout as checkout_service
    from sqlalchemy import select

    if outcome.get("outcome") != "AUTHORIZED":
        return outcome
    if payment_provider_name() != "mock":
        outcome["payment_note"] = "razorpay test mode: awaiting hosted checkout completion"
        return outcome
    txn_ref = outcome.get("txn_ref")
    order_id = outcome.get("order_id")
    if not txn_ref or not order_id:
        return outcome

    db = SessionLocal()
    try:
        session_row = db.scalar(select(AgentSession).where(AgentSession.session_id == session_id))
        merchant = db.scalar(select(Merchant).where(Merchant.id == session_row.merchant_id))
        result = checkout_service.complete_payment(
            db,
            session_row,
            merchant,
            txn_ref,
            order_id=order_id,
            payment_id=f"sim_{secrets.token_hex(6)}",
            signature="simulated",
        )
        outcome["outcome"] = "COMPLETED" if result.get("status") == "COMPLETED" else "PAYMENT_FAILED"
        outcome["payment_id"] = result.get("payment_id")
        outcome["completion"] = result
        return outcome
    except Exception as exc:
        logger.warning("auto-complete payment failed for %s: %s", txn_ref, exc)
        outcome["payment_note"] = f"auto-complete failed: {str(exc)[:160]}"
        return outcome
    finally:
        db.close()


async def run_transactable_session(
    ctx,  # RunContext of the buyer run
    *,
    persona: str,
    mission_prompt: str,
    persona_budget_minor: int | None = None,
    demo_scenario: str | None = None,
    force_browser: bool = False,
) -> dict:
    """Run ONE real gateway purchase session for this buyer run.

    Memory-informed intent -> live-web catalog materialization -> agent session
    (discover/search/quote/cart/checkout through the policy engine) -> mock
    payment auto-completion. Never raises into the agent; every failure mode is
    a structured, auditable outcome.

    `force_browser=True` (shopping mission): materialize via the managed stealth
    browser directly, and cap the buyer authorization at `persona_budget_minor`.
    """
    outcome: dict = {"attempted": False, "outcome": "NOT_TRANSACTABLE", "reasons": []}

    # 1. Memory-informed intent (Fleet B: the buyer knows the merchant first).
    memory_block = ""
    if ctx.memory:
        try:
            hits = await ctx.memory.search(ctx.merchant_id, mission_prompt, k=5)
            memory_block = "\n".join(f"- {h.text[:200]}" for h in hits)
        except Exception as exc:
            logger.info("buyer memory read failed: %s", exc)
    outcome["memory_hits"] = len(memory_block.splitlines())

    # 2. Materialize the live catalog from the merchant's real website
    #    (consumes the buyer run's tool budget; evidence rows are persisted).
    #    RunContext has no db handle (agents are db-agnostic by design), so the
    #    bridge owns the session: merchant lookup + materialization + the
    #    materializer's commits all run on one short-lived AsyncSession.
    from app.db.session import AsyncSessionLocal

    from app.intel.web_catalog import materialize_live_catalog

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        merchant_row = (
            await db.execute(select(Merchant).where(Merchant.id == ctx.merchant_id))
        ).scalar_one_or_none()
        if merchant_row is None:
            outcome["reasons"] = ["merchant row not found"]
            return outcome

        try:
            mat = await materialize_live_catalog(
                db,
                merchant_row,
                ctx.tools,
                llm=ctx.llm,
                query=mission_prompt,
                force_browser=force_browser,
            )
        except Exception as exc:
            logger.warning("live catalog materialization failed: %s", exc)
            outcome["reasons"] = [f"materialization failed: {str(exc)[:160]}"]
            return outcome
    outcome["materialized_products"] = [
        {"title": p.title, "url": p.url, "price_minor": p.price_minor, "method": p.method}
        for p in mat.products
    ]
    outcome["untransactable_reasons"] = mat.untransactable_reasons
    if not mat.transactable:
        outcome["outcome"] = "NOT_TRANSACTABLE"
        return outcome

    intent = (
        f"{mission_prompt}\n\nPersona: {persona}\n"
        + (f"Known context from previous missions:\n{memory_block}\n" if memory_block else "")
        + f"Live products observed on {getattr(merchant_row, 'name', 'the store')}: "
        + ", ".join(f"{p.title} @ ₹{p.price_minor / 100:,.0f}" for p in mat.products[:3])
    )

    # 3. Create the session + run the REAL gateway flow off the event loop.
    constraints = {
        "max_budget_minor": persona_budget_minor or DEFAULT_BUDGET_MINOR,
        "currency": "INR",
        "demo_scenario": demo_scenario,
    }
    try:
        session_id = await asyncio.to_thread(
            _start_session_sync, ctx.merchant_id, intent[:2000], constraints
        )
    except Exception as exc:
        logger.warning("transactable session start failed: %s", exc)
        outcome["outcome"] = "ERROR"
        outcome["reasons"] = [f"session start failed: {str(exc)[:160]}"]
        return outcome
    outcome["session_id"] = session_id

    events: list[dict] = []
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(_run_session_sync, session_id, events, loop, ctx.mission_id)
    outcome.update(_outcome_from_events(events))

    # 4. Headless payment completion on the mock rail (Razorpay stays pending).
    _auto_complete_payment_sync(session_id, outcome)
    return outcome