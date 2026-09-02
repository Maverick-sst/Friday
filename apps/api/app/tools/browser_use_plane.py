"""Browser Use Cloud tool plane (Fleet PRD B6).

Composes a base plane (Composio or mock) with the managed stealth browser as
the `browser_extract` backend. Every SEARCH/READ call delegates to the base
plane unchanged; `browser_extract` is the ONLY capability that touched the
cloud browser — keeping the composable plane contract honest and making the
browser feature swappable by swapping this wrapper.
"""

import logging
from collections.abc import Awaitable, Callable

from app.tools.browser_use_cloud import BrowserUseCloud
from app.tools.base import FetchResult, SearchHit, ToolPlane

logger = logging.getLogger("acg.tools.browser_use_plane")


class BrowserUseToolPlane:
    def __init__(self, base: ToolPlane) -> None:
        self._base = base
        self._browser = BrowserUseCloud()

    @property
    def name(self) -> str:
        return f"{self._base.name}+browser_use"

    async def search_web(self, query: str) -> list[SearchHit]:
        return await self._base.search_web(query)

    async def search_news(self, query: str) -> list[SearchHit]:
        return await self._base.search_news(query)

    async def search_shopping(self, query: str) -> list[SearchHit]:
        return await self._base.search_shopping(query)

    async def search_trends(self, query: str) -> list[SearchHit]:
        return await self._base.search_trends(query)

    async def search_reddit(self, query: str) -> list[SearchHit]:
        return await self._base.search_reddit(query)

    async def search_youtube(self, query: str) -> list[SearchHit]:
        return await self._base.search_youtube(query)

    async def search_social(self, query: str) -> list[SearchHit]:
        return await self._base.search_social(query)

    async def fetch_url(self, urls: list[str], max_chars: int = 6000) -> list[FetchResult]:
        return await self._base.fetch_url(urls, max_chars=max_chars)

    async def browser_extract(
        self,
        url: str,
        prompt: str | None = None,
        on_started: Callable[[str, str | None], Awaitable[None]] | None = None,
    ) -> tuple[dict | None, str | None] | dict | None:
        # Every failure mode becomes None (best-effort, never raises into the
        # agent) — consistent with the graceful-degradation contract of the
        # other planes.
        try:
            return await self._browser.extract_offer(url, prompt, on_started=on_started)
        except Exception as exc:
            logger.warning("browser_extract failed for %s: %s", url[:120], exc)
            return None