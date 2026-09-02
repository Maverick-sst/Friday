"""ToolRouter: per-agent scoped access to the tool plane (PRD_3 §12/§30).

- Enforces the agent capability allowlist (Strategy Agent has no live web).
- Enforces the run's tool-call budget at every call.
- Wraps results in ToolObservation so evidence can be persisted with
  provenance; tool failures become structured observations, never exceptions
  that crash a mission (PRD_3 §23.9 graceful degradation).
"""

import logging
import time
from collections.abc import Awaitable, Callable

from app.core.config import get_settings
from app.engine.context import RunBudget
from app.engine.progress import ProgressEvent, progress_bus
from app.tools.base import (
    AGENT_CAPABILITIES,
    CAPABILITIES,
    FetchResult,
    SearchHit,
    ToolObservation,
    ToolPlane,
)
from app.tools.mock_plane import MockToolPlane

logger = logging.getLogger("acg.tools.router")


class CapabilityDenied(PermissionError):
    pass


class ToolRouter:
    def __init__(
        self,
        plane: ToolPlane,
        *,
        agent_key: str,
        mission_id: str,
        budget: RunBudget,
    ) -> None:
        self._plane = plane
        self.agent_key = agent_key
        self.mission_id = mission_id
        self.budget = budget
        allowed = AGENT_CAPABILITIES.get(agent_key, set())
        if agent_key not in AGENT_CAPABILITIES:
            logger.warning("unknown agent_key %r: denying all capabilities", agent_key)
        self.allowed = allowed & CAPABILITIES
        # Per-run observation log (this router instance serves exactly one run).
        self.observations: list[ToolObservation] = []

    @property
    def plane_name(self) -> str:
        return self._plane.name

    async def _observe(self, capability: str, query_or_url: str) -> ToolObservation:
        if capability not in self.allowed:
            raise CapabilityDenied(
                f"agent {self.agent_key!r} may not use {capability!r} (allowed: {sorted(self.allowed)})"
            )
        self.budget.consume_tool_call()  # raises BudgetExhausted when done
        return ToolObservation(capability=capability, query_or_url=query_or_url)

    async def _finish(self, obs: ToolObservation, coro) -> ToolObservation:
        started = time.monotonic()
        self.observations.append(obs)
        # Observability: one `tool` observation per tool call (PRD 9.4/28). The
        # ToolRouter is the canonical boundary, so both Composio and mock planes
        # are covered without extra instrumentation.
        from app.observability import observation

        obs_attrs = {
            "tool_name": obs.capability,
            "agent_id": self.agent_key,
            "mission_id": self.mission_id,
        }
        try:
            with observation(name=f"tool.{obs.capability}", as_type="tool", input=obs.query_or_url[:200], **obs_attrs):
                result = await coro
                obs.latency_ms = int((time.monotonic() - started) * 1000)
                if isinstance(result, list) and result and isinstance(result[0], SearchHit):
                    obs.hits = result
                elif isinstance(result, list):
                    obs.text = " ".join(getattr(r, "text", "") for r in result)[:4000]
                else:
                    obs.text = str(result)[:4000]
            await self._emit(obs)
            return obs
        except Exception as exc:
            obs.ok = False
            obs.error = str(exc)[:300]
            obs.latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning("tool %s/%s failed: %s", self.agent_key, obs.capability, obs.error)
            await self._emit(obs)
            return obs

    async def _emit(self, obs: ToolObservation) -> None:
        label = (
            f"read {obs.query_or_url}"
            if obs.capability == "fetch_url"
            else f"{obs.capability}: {obs.query_or_url[:80]}"
        )
        status = "ok" if obs.ok else f"failed ({obs.error})"
        await progress_bus().publish(
            ProgressEvent(
                mission_id=self.mission_id,
                kind="tool_call",
                payload={
                    "agent": self.agent_key,
                    "capability": obs.capability,
                    "target": obs.query_or_url[:120],
                    "status": status,
                    "latency_ms": obs.latency_ms,
                    "plane": self.plane_name,
                    "budget_used": f"{self.budget.tool_calls_used}/{self.budget.max_tool_calls}",
                },
            )
        )
        logger.info("agent=%s %s -> %s", self.agent_key, label, status)

    # --- Public capability methods -------------------------------------------

    async def search_web(self, query: str) -> ToolObservation:
        return await self._finish(await self._observe("web_search", query), self._plane.search_web(query))

    async def search_news(self, query: str) -> ToolObservation:
        return await self._finish(await self._observe("news_search", query), self._plane.search_news(query))

    async def search_shopping(self, query: str) -> ToolObservation:
        return await self._finish(
            await self._observe("shopping_search", query), self._plane.search_shopping(query)
        )

    async def search_trends(self, query: str) -> ToolObservation:
        return await self._finish(
            await self._observe("trends_search", query), self._plane.search_trends(query)
        )

    async def search_reddit(self, query: str) -> ToolObservation:
        return await self._finish(
            await self._observe("reddit_search", query), self._plane.search_reddit(query)
        )

    async def search_youtube(self, query: str) -> ToolObservation:
        return await self._finish(
            await self._observe("youtube_search", query), self._plane.search_youtube(query)
        )

    async def search_social(self, query: str) -> ToolObservation:
        return await self._finish(
            await self._observe("social_search", query), self._plane.search_social(query)
        )

    async def fetch_url(self, urls: list[str]) -> list[FetchResult]:
        obs = await self._observe("fetch_url", ",".join(urls)[:300])
        finished = await self._finish_with_result(obs, self._plane.fetch_url(urls))
        return finished

    async def browser_extract(
        self,
        url: str,
        prompt: str | None = None,
        on_started: Callable[[str, str | None], Awaitable[None]] | None = None,
    ) -> tuple[dict | None, str | None] | dict | None:
        """Managed stealth-browser extract (B6), audited like any tool call.

        Returns (offer_dict | None, browser_run_id | None). When the cloud
        browser actually ran this surfaces the V4 run_id so the caller can
        construct a live-preview URL for the frontend. The run_id is None when
        the backend is not configured / produced no machine-readable price.

        Live preview (Fleet PRD B6): while the browser task executes, a
        `browser.session` frame is published on the progress bus carrying the
        run_id and (when the API provides one) the live-preview URL — the
        frontend renders a "watch live" link in the activity feed.
        """
        obs = await self._observe("browser_extract", url[:300])

        async def _on_started(run_id: str | None, preview_url: str | None) -> None:
            await progress_bus().publish(
                ProgressEvent(
                    mission_id=self.mission_id,
                    kind="browser.session",
                    payload={
                        "agent": self.agent_key,
                        "browser_run_id": run_id,
                        "preview_url": preview_url,
                        "target": url[:120],
                        "status": "live",
                    },
                )
            )
            if on_started is not None:
                await on_started(run_id, preview_url)

        result = await self._finish_with_result(
            obs, self._plane.browser_extract(url, prompt, on_started=_on_started)
        )
        if isinstance(result, tuple):
            return result  # (offer, run_id) from BrowserUseCloud
        return (result if isinstance(result, dict) else None, None)

    async def _finish_with_result(self, obs: ToolObservation, coro):
        """Like _finish but returns the raw tool result alongside the observation."""
        started = time.monotonic()
        from app.observability import observation

        obs_attrs = {
            "tool_name": obs.capability,
            "agent_id": self.agent_key,
            "mission_id": self.mission_id,
        }
        try:
            with observation(name=f"tool.{obs.capability}", as_type="tool", input=obs.query_or_url[:200], **obs_attrs):
                result = await coro
                obs.latency_ms = int((time.monotonic() - started) * 1000)
                if isinstance(result, list) and result and isinstance(result[0], FetchResult):
                    obs.text = " ".join(r.text for r in result)[:4000]
            await self._emit(obs)
            return result
        except Exception as exc:
            obs.ok = False
            obs.error = str(exc)[:300]
            obs.latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning("tool %s/%s failed: %s", self.agent_key, obs.capability, obs.error)
            await self._emit(obs)
            return []


def build_plane() -> ToolPlane:
    """Live Composio plane when configured; deterministic mock otherwise.

    The mock plane is also the runtime fallback if the live plane fails at
    construction time.

    B6: when Browser Use Cloud is configured, compose the base plane (Composio
    or mock) with a BrowserUseToolPlane so `browser_extract` escalates to the
    managed stealth browser while every SEARCH/READ call still goes to the base
    plane. The mock/composio planes themselves return None for browser_extract;
    this wrapper is what actually provides the browser backend.
    """
    settings = get_settings()
    base: ToolPlane
    if settings.composio_ready:
        from app.tools.composio_plane import ComposioToolPlane

        try:
            base = ComposioToolPlane()
        except Exception:
            logger.exception("composio plane construction failed; using mock")
            base = MockToolPlane()
    else:
        base = MockToolPlane()

    if settings.browser_use_ready:
        from app.tools.browser_use_plane import BrowserUseToolPlane

        return BrowserUseToolPlane(base)
    return base
