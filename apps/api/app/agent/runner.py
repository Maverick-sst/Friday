"""Agent session runner: drives a brain through the tool set, streaming events.

Security posture (PRD §21): the brain proposes, the gateway executes. Tools
are the ONLY surface; there is no path from here to Shopify credentials,
Razorpay secrets, or raw SQL.
"""

import time
from collections.abc import Iterator

from app.agent.base import AgentEvent, Tool, ToolContext
from app.agent.scripted_brain import ScriptedBrain
from app.agent.tools import build_tools
from app.core.config import get_settings
from app.demo.scenarios import maybe_apply_pre_checkout


def make_brain(intent: str, constraints: dict):
    settings = get_settings()
    # Try the LLM brain first: AGENT_LLM_* credentials, or the strategy fleet's
    # STRATEGY_LLM_* as a fallback (B4). Falls back to the deterministic
    # ScriptedBrain when no LLM credentials exist at all.
    if (settings.agent_llm_api_key or settings.strategy_llm_api_key) and (
        settings.agent_llm_base_url or settings.strategy_llm_base_url
    ):
        try:
            from app.agent.llm_brain import LLMBrain

            return LLMBrain(intent=intent, constraints=constraints), "llm"
        except RuntimeError:
            pass
    return ScriptedBrain(intent=intent, constraints=constraints), "scripted"


def _label_for(tool: Tool, args: dict) -> str:
    if tool.name == "discover_merchant":
        return "Discovering merchant profile..."
    if tool.name == "search_products":
        return f'Searching catalog for "{args.get("query", "")}"'
    if tool.name == "get_product":
        return "Fetching product details"
    if tool.name == "get_quote":
        return "Requesting live quote"
    if tool.name == "create_cart":
        return f"Creating cart for {args.get('quote_id')}"
    if tool.name == "checkout":
        return "Requesting checkout (policy gate)"
    return f"Calling {tool.name}"


def _summarize(tool: Tool, result: dict) -> str:
    name = tool.name
    if name == "discover_merchant":
        return f"Found merchant: {result.get('name')} ({result.get('category')})"
    if name == "search_products":
        titles = [p["title"] for p in (result.get("products") or [])[:3]]
        return f"{result.get('count', 0)} product(s): " + ", ".join(titles)
    if name == "get_quote":
        return (
            f"Live quote {result.get('quote_id')}: total "
            f"{_rupees(result.get('total_minor'))} ({result.get('currency')}), "
            f"inventory={'OK' if result.get('inventory_available') else 'UNAVAILABLE'}"
        )
    if name == "create_cart":
        return f"Cart {result.get('cart_id')} ready - {_rupees(result.get('total_minor'))}"
    if name == "checkout":
        if result.get("blocked"):
            codes = ", ".join(result.get("reason_codes") or [])
            return f"BLOCKED by policy engine [{codes}]"
        return f"Authorized {_rupees(result['payment_initiation']['amount_minor'])}; awaiting payment"
    return "done"


def _rupees(minor) -> str:
    if minor is None:
        return "?"
    return f"\u20b9{minor / 100:,.0f}"


def run_agent_session(
    db,
    session_row,
    merchant,
    stop_after_tool: str | None = None,
) -> Iterator[AgentEvent]:
    """Execute one buyer-agent run, yielding console events as they happen."""
    constraints = dict(session_row.constraints_json or {})
    intent = session_row.user_intent or ""
    brain, brain_kind = make_brain(intent, constraints)

    tools = {t.name: t for t in build_tools()}
    ctx = ToolContext(db=db, session_row=session_row, merchant=merchant)
    history: list[AgentEvent] = []

    yield AgentEvent(
        type="status",
        label=f"Buyer agent starting (brain={brain_kind})",
        payload={"intent": intent},
    )

    max_steps = 12
    for _ in range(max_steps):
        try:
            action = brain.next_action(history)  # type: ignore[union-attr]
            if action is None:
                break
            tool_name, args = action
        except LookupError as exc:
            yield AgentEvent(type="final", label=f"Stopped: {exc}", payload={"outcome": "stopped"})
            return
        except Exception as exc:
            yield AgentEvent(type="error", label=f"Planner error: {exc}")
            return

        tool = tools.get(tool_name)
        if tool is None:
            yield AgentEvent(type="error", label=f"Unknown tool proposed: {tool_name}")
            return

        # Deterministic failure-demo hook: mutate live state between quote and
        # checkout so the policy engine witnesses a genuine post-quote change.
        if tool_name == "checkout":
            scenario = maybe_apply_pre_checkout(db, session_row)
            if scenario:
                yield AgentEvent(
                    type="status",
                    label=f"Demo scenario active: {scenario} (live merchant state mutated)",
                    payload={"scenario": scenario},
                )

        yield AgentEvent(type="tool_call", tool=tool_name, label=_label_for(tool, args))

        started = time.perf_counter()
        error: str | None = None
        result: dict | None = None
        try:
            result = tool.fn(ctx, args)
        except Exception as exc:
            error = str(exc)

        latency_ms = int((time.perf_counter() - started) * 1000)

        if hasattr(brain, "record_tool_result"):
            try:
                brain.record_tool_result(result, error)  # type: ignore[union-attr]
            except RuntimeError as exc:
                yield AgentEvent(
                    type="final", label=f"Stopped: {str(exc)[:300]}", payload={"outcome": "error"}
                )
                return

        if error is not None:
            event = AgentEvent(
                type="tool_result",
                tool=tool_name,
                label=f"Failed: {error[:200]}",
                payload={"error": error, "latency_ms": latency_ms},
            )
            history.append(event)
            yield event
            continue

        summary = _summarize(tool, result or {})
        event = AgentEvent(
            type="tool_result",
            tool=tool_name,
            label=summary,
            payload={"result": result, "latency_ms": latency_ms},
        )
        history.append(event)
        yield event

        if stop_after_tool is not None and tool_name == stop_after_tool:
            break

        # Terminal conditions.
        if tool_name == "checkout":
            blocked = bool((result or {}).get("blocked"))
            status = (result or {}).get("status")
            outcome = "BLOCKED" if blocked else ("PAYMENT_PENDING" if status else "UNKNOWN")
            payload = {"outcome": outcome}
            selection = getattr(brain, "selection_summary", None)
            if callable(selection):
                payload["selection"] = selection()
            yield AgentEvent(
                type="final",
                label=(
                    "Transaction BLOCKED - no payment was attempted."
                    if blocked
                    else "Checkout authorized - proceed to payment."
                ),
                payload=payload,
            )
            return

    yield AgentEvent(type="final", label="Run ended.", payload={"outcome": "incomplete"})
