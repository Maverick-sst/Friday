"""The five specialist agents (PRD_3 §7).

Each agent is thin: build messages from merchant context + tool observations,
call the LLM through the shared provider, return a validated schema. Research
loops use the cheapest capability first per PRD_3 §13.
"""

import asyncio
import contextlib
import json
import logging

from pydantic import BaseModel

from app.agents.base import AgentContract, BaseSpecialistAgent
from app.core.config import get_settings
from app.intel import prompts
from app.intel.identity import domain_of
from app.intel.schemas import BuyerSimulationOutput, ResearchOutput, StrategySynthesisOutput
from app.tools.router import ToolRouter

logger = logging.getLogger("acg.intel.agents")


# --- Merchant-aware query grounding (FIX_PRD_1 §9/§10) -----------------------


def _identity_of(ctx) -> dict:
    ident = ctx.merchant_context.get("identity")
    return ident if isinstance(ident, dict) else {}


def _identity_summary(ctx) -> str:
    ident = _identity_of(ctx)
    if not ident:
        return "unresolved (use URL-derived context only)"
    bits = [str(ident.get("canonical_name") or "unknown")]
    if ident.get("domain"):
        bits.append(f"domain {ident['domain']}")
    if ident.get("primary_category"):
        bits.append(f"category {ident['primary_category']}")
    if ident.get("geography"):
        bits.append(f"geography {ident['geography']}")
    if ident.get("known_product_types"):
        bits.append("products: " + ", ".join(str(p) for p in ident["known_product_types"][:5]))
    bits.append(f"identity confidence {ident.get('identity_confidence', 0)}")
    return "; ".join(bits)


def _grounded(ctx, tail: str = "") -> str:
    """Merchant-aware query grounding (FIX_PRD_1 §10).

    High-confidence identity: quoted canonical name + category + geography.
    Degraded identity: anchor on the domain instead of an ambiguous brand
    name — "Snitch competitors" collides with unrelated entities that merely
    share the name.
    """
    ident = _identity_of(ctx)
    mc = ctx.merchant_context
    settings = get_settings()
    degraded = float(ident.get("identity_confidence") or 0.0) < settings.identity_confidence_threshold
    parts: list[str] = []
    if degraded:
        domain = ident.get("domain") or domain_of(mc.get("website_url") or "")
        if domain:
            parts.append(f'"{domain}"')
    else:
        name = ident.get("canonical_name") or mc.get("name")
        if name:
            parts.append(f'"{name}"')
    category = ident.get("primary_category") or mc.get("category")
    if category:
        parts.append(str(category))
    geography = ident.get("geography")
    if geography:
        parts.append(str(geography))
    if tail:
        parts.append(tail)
    return " ".join(parts) or (tail or str(mc.get("name") or ""))


def _observations_block(observations: list) -> str:
    parts = []
    for i, obs in enumerate(observations):
        if obs.capability == "fetch_url":
            body = (obs.text or "")[:1500]
            parts.append(f"[{i}] FETCHED {obs.query_or_url}:\n{body}")
        else:
            lines = []
            for h in obs.hits[:6]:
                pub = f" ({h.published_at})" if h.published_at else ""
                lines.append(f"  - {h.url or 'no-url'} | {h.title}{pub}\n    {h.snippet[:280]}")
            label = (obs.error and f"FAILED: {obs.error}") or "results:"
            parts.append(
                f'[{i}] {obs.capability.upper()} "{obs.query_or_url}" -> {label}\n' + "\n".join(lines)
            )
    return "\n\n".join(parts) or "(no observations recorded)"


# --- Bounded sub-agent spawning (Fleet PRD A2; OTEL PRD §9.10/§32) -----------


def _top_hit_topic(observations: list) -> str | None:
    """Most prominent source title from a research loop, for scout assignment."""
    for obs in observations:
        if obs.capability != "fetch_url":
            for h in obs.hits:
                if h.url and h.title:
                    return h.title[:140]
    return None


async def _spawn_scout(ctx, objective: str, reason: str) -> dict | None:
    """Spawn ONE depth-1 scout child for a narrow deep-dive.

    Spawn safety (OTEL PRD §32): only depth-0 parents may spawn, only when
    settings.max_sub_agent_depth >= 1, one scout per parent run. The child
    claims its own mission budget (execute_agent_run) so a starved mission
    simply rejects the spawn. Never raises into the parent (PRD 21). Every
    transition emits `agent.spawn.*` SSE events for trace correlation.
    """
    settings = get_settings()
    if ctx.depth != 0 or settings.max_sub_agent_depth < 1:
        return None
    from app.engine.progress import ProgressEvent, progress_bus

    spawn_payload = {
        "run_id": ctx.run_id,
        "agent": ctx.agent_key,
        "child_agent": "scout",
        "reason": reason[:200],
        "depth": ctx.depth + 1,
    }
    await progress_bus().publish(
        ProgressEvent(mission_id=ctx.mission_id, kind="agent.spawn.requested", payload=dict(spawn_payload))
    )
    try:
        # Lazy import: handlers imports this module for REGISTRY.
        from app.intel.handlers import execute_agent_run

        result = await execute_agent_run(
            mission_id=ctx.mission_id,
            merchant_id=ctx.merchant_id,
            agent_key="scout",
            objective=objective[:400],
            depth=ctx.depth + 1,
            parent_run_id=ctx.run_id,
            extra={"spawn_reason": reason[:200], "parent_agent": ctx.agent_key},
        )
    except Exception as exc:
        logger.warning("scout spawn by %s failed: %s", ctx.agent_key, exc)
        await progress_bus().publish(
            ProgressEvent(
                mission_id=ctx.mission_id,
                kind="agent.spawn.rejected",
                payload={**spawn_payload, "error": str(exc)[:200]},
            )
        )
        return None
    await progress_bus().publish(
        ProgressEvent(
            mission_id=ctx.mission_id,
            kind="agent.spawn.completed",
            payload={**spawn_payload, "ok": bool(result.get("ok"))},
        )
    )
    return result


class _ResearchAgent(BaseSpecialistAgent):
    """Shared skeleton for market/competitor/presence research loops."""

    output_schema: type[BaseModel] = ResearchOutput

    async def run_research(self, ctx, searches: list[tuple[str, str]]) -> tuple[list, str]:
        """Execute capability->query pairs; returns (observations, block)."""
        from app.tools.base import ToolObservation

        router: ToolRouter = ctx.tools
        observations: list = []
        for capability, query in searches:
            try:
                method = getattr(router, capability)
                observations.append(await method(query))
            except Exception as exc:
                logger.info("%s research loop stopped: %s", ctx.agent_key, exc)
                break
        fetch_targets = [
            h.url
            for obs in observations
            if obs.capability != "fetch_url"
            for h in obs.hits
            if h.url and h.source != "trends"  # any fetchable source incl. reddit/youtube/social
        ][:3]
        for url in fetch_targets:
            try:
                pages = await router.fetch_url([url])
                if pages:
                    observations.append(
                        ToolObservation(
                            capability="fetch_url",
                            query_or_url=url,
                            ok=True,
                            text=pages[0].text[:2000],
                        )
                    )
            except Exception as exc:
                logger.info("fetch %s failed: %s", url, exc)
        return observations, _observations_block(observations)

    async def _remember(self, ctx, result, label: str = "Finding") -> None:
        if ctx.memory:
            for finding in result.findings[:3]:
                await ctx.memory.add(
                    ctx.merchant_id,
                    f"{label}: {finding.title}. {finding.statement}",
                    kind="observation",
                    mission_id=ctx.mission_id,
                )

    def _merchant_block(self, ctx) -> str:
        mc = ctx.merchant_context
        return prompts.MERCHANT_CONTEXT_TMPL.format(
            name=mc.get("name", "unknown"),
            website_url=mc.get("website_url", "unknown"),
            category=mc.get("category") or "unknown",
            description=(mc.get("description") or "n/a")[:400],
            goal=mc.get("goal_text") or "not stated",
            competitors=", ".join(mc.get("competitors", [])[:5]) or "unknown",
            baseline_summary=(mc.get("baseline_summary") or "none yet")[:600],
            identity_summary=_identity_summary(ctx),
        )

    def _messages(self, ctx, objective: str, observations_block: str):
        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{self._merchant_block(ctx)}\n\nMISSION OBJECTIVE\n{objective}\n\n"
                    f"TOOL OBSERVATIONS\n{observations_block}\n\n"
                    f"{prompts.RELEVANCE_RULES}\n\n"
                    "Produce findings now. Cite claim_indexes against the numbered "
                    "observations and set entity_relevance for every claim."
                ),
            },
        ]

    async def _execute(self, ctx, **kwargs):  # pragma: no cover - subclasses implement
        raise NotImplementedError


class MarketIntelligenceAgent(_ResearchAgent):
    SYSTEM_PROMPT = prompts.MARKET_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Market Intelligence",
            key="market",
            role="Understand the external market around the merchant.",
            purpose="Category trends, new entrants, changing needs, opportunities, threats.",
            allowed_tools=["web_search", "news_search", "trends_search", "youtube_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_trends", _grounded(ctx, "market trends")),
                ("search_news", _grounded(ctx, "market news")),
                ("search_youtube", _grounded(ctx, "market review")),
                ("search_web", _grounded(ctx, ctx.objective[:120])),
            ],
        )
        # Fleet PRD A2: bounded spawn — one scout deep-dives the most prominent
        # signal found (OTEL PRD §32: parent/child/reason/depth recorded). The
        # scout runs CONCURRENTLY with the parent's synthesis call so parent +
        # child together stay inside the parent's single wait_for ceiling.
        topic = _top_hit_topic(_observations)
        scout_task = (
            asyncio.create_task(
                _spawn_scout(
                    ctx,
                    f"Verify and deep-dive this emerging market signal: {topic}",
                    reason=f"top signal from market research: {topic[:80]}",
                )
            )
            if topic
            else None
        )
        try:
            result, raw = await llm.structured_generate(
                self._messages(ctx, ctx.objective, block), self.output_schema
            )
        except BaseException:
            if scout_task is not None:
                scout_task.cancel()
            raise
        if scout_task is not None:
            with contextlib.suppress(Exception):
                await scout_task
        await self._remember(ctx, result, label="Market finding")
        return result, raw


class CompetitorIntelligenceAgent(MarketIntelligenceAgent):
    SYSTEM_PROMPT = prompts.COMPETITOR_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Competitor Intelligence",
            key="competitor",
            role="Understand competitors and competitive position changes.",
            purpose="Pricing, products, positioning, reviews, advantages, weaknesses.",
            allowed_tools=["web_search", "news_search", "shopping_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        competitors = ", ".join(ctx.merchant_context.get("competitors", [])[:4]) or "main competitors"
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_shopping", _grounded(ctx)),
                ("search_web", _grounded(ctx, f"{competitors} competitors pricing positioning reviews")),
            ],
        )
        # Fleet PRD A2: bounded spawn — one scout verifies the top competitor
        # signal concurrently with the parent's synthesis call (keeps parent +
        # child inside the parent's single wait_for ceiling).
        topic = _top_hit_topic(_observations)
        scout_task = (
            asyncio.create_task(
                _spawn_scout(
                    ctx,
                    f"Verify and deep-dive this competitor signal: {topic}",
                    reason=f"top signal from competitor research: {topic[:80]}",
                )
            )
            if topic
            else None
        )
        try:
            result, raw = await llm.structured_generate(
                self._messages(ctx, ctx.objective, block), self.output_schema
            )
        except BaseException:
            if scout_task is not None:
                scout_task.cancel()
            raise
        if scout_task is not None:
            with contextlib.suppress(Exception):
                await scout_task
        await self._remember(ctx, result, label="Competitor finding")
        return result, raw


class PresenceAgent(MarketIntelligenceAgent):
    SYSTEM_PROMPT = prompts.PRESENCE_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Digital Presence / Reputation",
            key="presence",
            role="Understand merchant perception across the public internet.",
            purpose="Reviews, community, press, search results, trust signals.",
            allowed_tools=["web_search", "news_search", "reddit_search", "social_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_web", _grounded(ctx, "customer reviews")),
                ("search_reddit", _grounded(ctx, "customer experiences opinions")),
                ("search_social", _grounded(ctx, "official social presence")),
                ("search_news", _grounded(ctx)),
            ],
        )
        result, raw = await llm.structured_generate(
            self._messages(ctx, ctx.objective, block), self.output_schema
        )
        await self._remember(ctx, result, label="Presence finding")
        return result, raw


def _usable_urls(observations: list) -> list[str]:
    """Deduplicated, ordered URLs from successful search observations.

    Failed observations (obs.ok False) contribute nothing, so a dead tool
    call can never seed fetch targets.
    """
    urls: list[str] = []
    for obs in observations:
        if not obs.ok:
            continue
        for h in obs.hits:
            if h.url and h.url not in urls:
                urls.append(h.url)
    return urls


class ReviewsAgent(_ResearchAgent):
    """Community sentiment: Reddit threads, YouTube reviews, social chatter."""

    SYSTEM_PROMPT = prompts.REVIEWS_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Reviews & Community Sentiment",
            key="reviews",
            role="Surface what real customers say in communities and reviews.",
            purpose="Reddit/YouTube/social voice-of-customer: complaint themes, praise themes, prevalence.",
            allowed_tools=["reddit_search", "youtube_search", "social_search", "news_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_reddit", _grounded(ctx, "customer reviews experiences")),
                ("search_youtube", _grounded(ctx, "review")),
                ("search_social", _grounded(ctx, "customer feedback")),
            ],
        )
        result, raw = await llm.structured_generate(
            self._messages(ctx, ctx.objective, block), self.output_schema
        )
        await self._remember(ctx, result, label="Reviews finding")
        return result, raw


class AdsIntelligenceAgent(_ResearchAgent):
    """Promotional landscape: social ad surfaces, offers, creative messaging."""

    SYSTEM_PROMPT = prompts.ADS_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Ads & Promotions Intelligence",
            key="ads",
            role="Map merchant and competitor promotions, offers and ad messaging.",
            purpose="Active social ads, discount cadence, urgency tactics, creative freshness.",
            allowed_tools=["social_search", "web_search", "news_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_social", _grounded(ctx, "ads offers promotions")),
                ("search_web", _grounded(ctx, "sale discount offer campaign")),
            ],
        )
        result, raw = await llm.structured_generate(
            self._messages(ctx, ctx.objective, block), self.output_schema
        )
        await self._remember(ctx, result, label="Ads finding")
        return result, raw


class CatalogScanAgent(_ResearchAgent):
    """Concrete catalogue/pricing scan: merchant + competitor product rows."""

    SYSTEM_PROMPT = prompts.CATALOG_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Catalog Scan",
            key="catalog",
            role="Scan what is actually being sold and at what price.",
            purpose="Product rows, price points, ratings, availability, buyer-relevant policy signals.",
            allowed_tools=["shopping_search", "web_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        _observations, block = await self.run_research(
            ctx,
            [
                ("search_shopping", _grounded(ctx)),
                ("search_web", _grounded(ctx, "product catalogue prices")),
            ],
        )
        result, raw = await llm.structured_generate(
            self._messages(ctx, ctx.objective, block), self.output_schema
        )
        await self._remember(ctx, result, label="Catalog finding")
        return result, raw


class ScoutAgent(_ResearchAgent):
    """Lightweight depth-1 child spawned by a parent specialist (Fleet PRD A2).

    Single-purpose: deep-dive the ONE objective assigned by the parent.
    Children never spawn (star pattern, PRD_3 §11) — enforced by the depth
    guard in _spawn_scout, not just by convention.
    """

    SYSTEM_PROMPT = prompts.SCOUT_SYSTEM
    output_schema = ResearchOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Research Scout",
            key="scout",
            role="Execute one focused deep-dive assigned by a parent agent.",
            purpose="Verify and expand a single signal from parent research; 1-3 evidence-backed findings.",
            allowed_tools=["web_search", "news_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        focus = ctx.objective
        _observations, block = await self.run_research(ctx, [("search_web", focus[:200])])
        result, raw = await llm.structured_generate(
            self._messages(ctx, focus, block), self.output_schema
        )
        await self._remember(ctx, result, label="Scout finding")
        return result, raw


class BuyerSimulationAgent(BaseSpecialistAgent):
    SYSTEM_PROMPT = prompts.BUYER_SYSTEM
    output_schema = BuyerSimulationOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="AI Buyer Simulation",
            key="buyer",
            role="Simulate realistic prospective buyers completing purchase missions.",
            purpose="Evaluate how the merchant performs across realistic buyer missions.",
            allowed_tools=["web_search", "shopping_search", "fetch_url"],
            mission_types=["baseline", "recurring", "on_demand", "experiment"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        router: ToolRouter = ctx.tools
        persona = ctx.extra.get("persona") or (
            "A price-conscious beginner customer buying online, values comfort, "
            "trust signals and fast delivery with a clear return policy."
        )
        mission_prompt = ctx.extra.get("buyer_mission") or ctx.objective
        personas: list[str] = ctx.extra.get("personas") or [persona][:1]
        demo_scenario = ctx.extra.get("demo_scenario")
        # Shopping mission (B7): structured shopper intent + explicit budget and
        # browser-first materialization. Empty/absent = normal buyer simulation.
        shopping: dict = ctx.extra.get("shopping") or {}
        force_browser = bool(ctx.extra.get("force_browser") or shopping.get("force_browser"))
        persona_budget_minor = shopping.get("budget_minor")  # in paise
        if persona_budget_minor is not None:
            try:
                persona_budget_minor = int(persona_budget_minor)
            except (TypeError, ValueError):
                persona_budget_minor = None

        # Fleet PRD B3: REAL transactable attempts. For each persona: memory ->
        # live-web materialization (real product pages, real prices) -> gateway
        # session (discover/search/quote/cart/checkout -> policy -> payment).
        # All tool calls here are audited observations on this run's budget.
        from app.intel.transact import run_transactable_session

        outcomes: list[dict] = []
        for p in personas[:2]:
            try:
                outcome = await run_transactable_session(
                    ctx,
                    persona=p,
                    mission_prompt=mission_prompt,
                    demo_scenario=demo_scenario,
                    persona_budget_minor=persona_budget_minor,
                    force_browser=force_browser,
                )
            except Exception as exc:  # never crash the run on bridge trouble
                logger.warning("transactable session failed for persona: %s", exc)
                outcome = {"attempted": True, "outcome": "ERROR", "reasons": [str(exc)[:200]]}
            outcome["persona"] = p[:120]
            outcomes.append(outcome)
            if ctx.memory:
                try:
                    await ctx.memory.add(
                        ctx.merchant_id,
                        f"Buyer transaction attempt ({p[:60]}): {outcome.get('outcome')} "
                        + "; ".join(outcome.get("blocked_reason_codes", []) or outcome.get("untransactable_reasons", [])[:1]),
                        kind="outcome",
                        mission_id=ctx.mission_id,
                    )
                except Exception:
                    pass

        txn_block = json.dumps(outcomes, default=str)[:2200]
        result, raw = await llm.structured_generate(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"{prompts.MERCHANT_CONTEXT_TMPL.format(**_merchant_kwargs(ctx))}\n\n"
                        f"BUYER PERSONA(S)\n{json.dumps(personas, default=str)}\n\n"
                        f"PURCHASE MISSION\n{mission_prompt}\n\n"
                        f"TRANSACTION ATTEMPT RESULTS (authoritative, from the gateway)\n{txn_block}\n\n"
                        "Analyze how each persona's purchase attempt went: what the gateway "
                        "materialized from the real site, whether checkout was authorized, "
                        "blocked (and why), or the site was not AI-transactable. Turn every "
                        f"blocked reason code and untransactable reason into friction findings.\n"
                        f"{prompts.RELEVANCE_RULES}"
                    ),
                },
            ],
            self.output_schema,
        )
        # Factual outcome attached by the agent, overwriting anything the LLM guessed.
        result.transaction = {"sessions": outcomes}
        if ctx.memory and result.selected:
            await ctx.memory.add(
                ctx.merchant_id,
                f"Buyer simulation '{mission_prompt[:80]}': selected {result.selected}. "
                + "; ".join(result.friction_observed[:2]),
                kind="outcome",
                mission_id=ctx.mission_id,
            )
        return result, raw


def _merchant_kwargs(ctx) -> dict:
    mc = ctx.merchant_context
    return {
        "name": mc.get("name", "unknown"),
        "website_url": mc.get("website_url", ""),
        "category": mc.get("category"),
        "description": mc.get("description"),
        "goal": mc.get("goal_text"),
        "competitors": json.dumps(mc.get("competitors", [])),
        "baseline_summary": mc.get("baseline_summary") or "",
        "identity_summary": _identity_summary(ctx),
    }


class StrategyAgent(BaseSpecialistAgent):
    """Not a web researcher: consumes stored intelligence only (PRD_3 §7.5)."""

    SYSTEM_PROMPT = prompts.STRATEGY_SYSTEM
    output_schema = StrategySynthesisOutput

    @property
    def contract(self) -> AgentContract:
        return AgentContract(
            name="Strategy",
            key="strategy",
            role="Turn accumulated intelligence into prioritized strategic decisions.",
            purpose="Synthesize specialist outputs into ranked, evidence-backed recommendations.",
            allowed_tools=["memory"],  # no live web access by default
            mission_types=["baseline", "recurring", "on_demand"],
        )

    async def _execute(self, ctx, **kwargs):
        llm = ctx.llm
        findings_block = ctx.extra.get("findings_block") or "(no prior findings provided)"
        evidence_note = ctx.extra.get("evidence_note") or ""
        memory_hits = []
        if ctx.memory:
            try:
                memory_hits = await ctx.memory.search(ctx.merchant_id, ctx.objective, k=5)
            except Exception:
                pass
        memory_block = "\n".join(f"- {h.text}" for h in memory_hits) or "(no memories)"
        result, raw = await llm.structured_generate(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"{prompts.MERCHANT_CONTEXT_TMPL.format(**_merchant_kwargs(ctx))}\n\n"
                        f"MISSION OBJECTIVE\n{ctx.objective}\n\n"
                        f"SPECIALIST FINDINGS (with severity/confidence)\n{findings_block}\n\n"
                        f"EVIDENCE SUMMARY\n{evidence_note or 'see findings'}\n\n"
                        f"PERSISTENT MEMORY\n{memory_block}\n\n"
                        "Produce ranked recommendations now."
                    ),
                },
            ],
            self.output_schema,
        )
        if ctx.memory:
            for rec in result.recommendations[:3]:
                await ctx.memory.add(
                    ctx.merchant_id,
                    f"Recommendation proposed: {rec.recommendation_text[:200]}",
                    kind="outcome",
                    mission_id=ctx.mission_id,
                )
        return result, raw


REGISTRY = {
    "market": MarketIntelligenceAgent(),
    "competitor": CompetitorIntelligenceAgent(),
    "buyer": BuyerSimulationAgent(),
    "presence": PresenceAgent(),
    "strategy": StrategyAgent(),
    "reviews": ReviewsAgent(),
    "ads": AdsIntelligenceAgent(),
    "catalog": CatalogScanAgent(),
    "scout": ScoutAgent(),
}
