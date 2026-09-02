"""Live Composio tool plane (REST v3.1, no-auth composio_search toolkit).

PRD_3 §12: Composio is the capability layer, not the mission engine. This
plane only wraps SEARCH/READ web tools; per-agent restriction lives in
ToolRouter + AGENT_CAPABILITIES, never here.

Every external call: explicit timeout, bounded retries with backoff+jitter on
429/5xx/network errors, structured errors (never raises into agent logic).
"""

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.tools.base import FetchResult, SearchHit

logger = logging.getLogger("acg.tools.composio")

_BASE = "https://backend.composio.dev/api/v3.1"
_TRANSIENT = {408, 425, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class ComposioError(Exception):
    pass


class ComposioToolPlane:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=_BASE,
            headers={"x-api-key": settings.composio_api_key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    @property
    def name(self) -> str:
        return "composio"

    async def _execute(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        last_error = "unknown"
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._client.post(
                    f"/tools/execute/{slug}",
                    json={"arguments": arguments, "version": "latest"},
                )
                if resp.status_code == 200:
                    logger.info(
                        "composio %s ok latency_ms=%d", slug, int((time.monotonic() - started) * 1000)
                    )
                    return resp.json().get("data") or {}
                if resp.status_code in _TRANSIENT and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(min(1.5 * (2**attempt), 6.0) + random.uniform(0, 0.5))
                    last_error = f"http {resp.status_code}"
                    continue
                raise ComposioError(f"{slug} failed: http {resp.status_code}: {resp.text[:200]}")
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = type(exc).__name__
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(min(1.5 * (2**attempt), 6.0) + random.uniform(0, 0.5))
        raise ComposioError(f"{slug} failed after retries: {last_error}")

    async def search_web(self, query: str) -> list[SearchHit]:
        data = await self._execute("COMPOSIO_SEARCH_WEB", {"query": query})
        hits: list[SearchHit] = []
        for c in data.get("citations") or []:
            url = c.get("id") or c.get("url") or ""
            if url:
                hits.append(
                    SearchHit(
                        url=url,
                        title=c.get("title") or url,
                        snippet=(c.get("content") or c.get("snippet") or "")[:400],
                        published_at=c.get("publishedDate"),
                        source="web",
                    )
                )
        # The synthesized answer is useful too; keep it as a synthetic hit.
        if data.get("answer"):
            hits.insert(
                0,
                SearchHit(
                    url="", title="Synthesized answer", snippet=str(data["answer"])[:800], source="web"
                ),
            )
        return hits

    async def _serp_like(self, slug: str, query: str, source: str) -> list[SearchHit]:
        data = await self._execute(slug, {"query": query})
        raw = data.get("results") or data.get("organic_results") or data.get("news_results") or []
        # Some tools return a dict wrapper; flatten any nested lists defensively.
        if isinstance(raw, dict):
            for value in raw.values():
                if isinstance(value, list):
                    raw = value
                    break
        hits = []
        for r in raw[:10]:
            url = r.get("url") or r.get("link") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=r.get("title") or url,
                    snippet=(r.get("snippet") or r.get("description") or r.get("content") or "")[:400],
                    published_at=r.get("date") or r.get("published_date"),
                    source=source,
                )
            )
        return hits

    async def search_news(self, query: str) -> list[SearchHit]:
        return await self._serp_like("COMPOSIO_SEARCH_NEWS", query, "news")

    async def search_shopping(self, query: str) -> list[SearchHit]:
        data = await self._execute("COMPOSIO_SEARCH_SHOPPING", {"query": query})
        hits: list[SearchHit] = []
        # Shape: data.results.categorized_shopping_results[].shopping_results[]
        categories = ((data.get("results") or {}).get("categorized_shopping_results")) or []
        for category in categories:
            for item in (category.get("shopping_results") or [])[:6]:
                url = item.get("product_link") or ""
                if not url:
                    continue
                price = item.get("price") or (
                    f"INR {item['extracted_price']}" if item.get("extracted_price") else ""
                )
                title = item.get("title") or item.get("product_id") or "shopping result"
                snippet_parts = [p for p in (price, item.get("source"), *(item.get("extensions") or [])) if p]
                rating = item.get("rating")
                if rating:
                    snippet_parts.append(f"{rating} stars")
                hits.append(
                    SearchHit(
                        url=url,
                        title=str(title)[:200],
                        snippet=" | ".join(str(p) for p in snippet_parts)[:400],
                        source="shopping",
                    )
                )
        return hits

    async def search_trends(self, query: str) -> list[SearchHit]:
        data = await self._execute("COMPOSIO_SEARCH_TRENDS", {"query": query})
        text = str(data.get("answer") or data.get("trend_data") or data)[:600]
        return [SearchHit(url="", title=f"Trend signal: {query}", snippet=text, source="trends")]

    # --- Source-scoped searches (Fleet PRD A1 Phase A) ------------------------
    #
    # Generic SERP filtered to one platform via query rewriting: zero new
    # credentials, reuses the no-auth composio_search toolkit. Phase B will
    # dispatch to native Reddit/YouTube/Meta toolkits instead, behind
    # settings.toolkit_native_social_enabled (stubbed there, not here, so the
    # plane stays config-free).

    _SCOPED_SITES = {
        "reddit": "site:reddit.com",
        "youtube": "site:youtube.com",
        "social": "(site:instagram.com OR site:facebook.com)",
    }

    async def _scoped_search(self, scope: str, query: str) -> list[SearchHit]:
        site = self._SCOPED_SITES[scope]
        data = await self._execute("COMPOSIO_SEARCH_WEB", {"query": f"{query} {site}"})
        # COMPOSIO_SEARCH_WEB returns a `citations` list; some wrappers return
        # serp-like `results`/`organic_results` — accept both shapes.
        raw = data.get("citations") or data.get("results") or data.get("organic_results") or []
        if isinstance(raw, dict):
            for value in raw.values():
                if isinstance(value, list):
                    raw = value
                    break
        hits: list[SearchHit] = []
        for r in raw[:10]:
            url = r.get("url") or r.get("link") or r.get("id") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=r.get("title") or url,
                    snippet=(
                        r.get("content") or r.get("snippet") or r.get("description") or ""
                    )[:400],
                    published_at=r.get("publishedDate") or r.get("date"),
                    source=scope,
                )
            )
        return hits

    async def search_reddit(self, query: str) -> list[SearchHit]:
        return await self._scoped_search("reddit", query)

    async def search_youtube(self, query: str) -> list[SearchHit]:
        return await self._scoped_search("youtube", query)

    async def search_social(self, query: str) -> list[SearchHit]:
        return await self._scoped_search("social", query)


    async def fetch_url(self, urls: list[str], max_chars: int = 6000) -> list[FetchResult]:
        if not urls:
            return []
        data = await self._execute(
            "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
            {"urls": urls[:5], "text": True, "max_characters": max_chars},
        )
        out: list[FetchResult] = []
        for r in data.get("results") or []:
            url = r.get("id") or r.get("url") or ""
            text = r.get("text") or ""
            if url and text:
                truncated = len(text) >= max_chars
                out.append(FetchResult(url=url, text=text[:max_chars], truncated=truncated))
        return out

    async def browser_extract(self, url: str, prompt: str | None = None, on_started=None) -> dict | None:
        # Composio is SEARCH/READ only; the managed stealth browser (Browser Use
        # Cloud) is the browser_extract backend, wired in build_plane (B6).
        return None
