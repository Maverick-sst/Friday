"""Web tool capabilities for the AI team (PRD_3 §13).

Three conceptual levels, cheapest-first:
    SEARCH -> find sources        READ -> fetch/extract content      ACT -> browse

MVP ships SEARCH + READ through Composio (no-auth composio_search toolkit);
ACT-level browsing is deliberately deferred. Every result preserves the URL
and observation time so agents can emit provenance-backed evidence.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    published_at: str | None = None
    source: str = "web"  # web|news|shopping|trends|ddg|reddit|youtube|social


@dataclass(slots=True)
class FetchResult:
    url: str
    text: str
    truncated: bool = False


@dataclass(slots=True)
class ToolObservation:
    """Everything needed to persist an Evidence row from one tool call."""

    capability: str
    query_or_url: str
    ok: bool = True
    hits: list[SearchHit] = field(default_factory=list)
    text: str | None = None
    error: str | None = None
    latency_ms: int = 0
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_evidence_claim(self) -> str:
        if self.capability == "fetch_url":
            return f"Read {self.query_or_url}"
        return f"Searched ({self.capability}): {self.query_or_url}"


class ToolPlane(Protocol):
    """Provider-agnostic web capability surface."""

    async def search_web(self, query: str) -> list[SearchHit]: ...
    async def search_news(self, query: str) -> list[SearchHit]: ...
    async def search_shopping(self, query: str) -> list[SearchHit]: ...
    async def search_trends(self, query: str) -> list[SearchHit]: ...
    # Source-scoped searches (Fleet PRD A1 Phase A): generic SERP filtered to
    # one platform via query rewriting — no extra auth. Phase B swaps the
    # implementation for native toolkits behind `toolkit_native_social_enabled`.
    async def search_reddit(self, query: str) -> list[SearchHit]: ...
    async def search_youtube(self, query: str) -> list[SearchHit]: ...
    async def search_social(self, query: str) -> list[SearchHit]: ...
    async def fetch_url(self, urls: list[str], max_chars: int = 6000) -> list[FetchResult]: ...
    # Managed stealth-browser extraction (Fleet PRD B6): render a page that
    # blocks static fetches and return a structured offer dict, or None when
    # no machine-readable price exists. Browser-backends return the tuple
    # (offer, run_id); no-op planes return None. `on_started(run_id,
    # preview_url)` fires as soon as the browser session is live so callers
    # can surface a live viewer (Fleet PRD B6 live-preview).
    async def browser_extract(
        self,
        url: str,
        prompt: str | None = None,
        on_started: Callable[[str, str | None], Awaitable[None]] | None = None,
    ) -> tuple[dict | None, str | None] | dict | None: ...
    @property
    def name(self) -> str: ...


CAPABILITIES = {
    "web_search",
    "news_search",
    "shopping_search",
    "trends_search",
    "ddg_search",
    "reddit_search",
    "youtube_search",
    "social_search",
    "fetch_url",
    "browser_extract",
}

# PRD_3 §30 permission matrix (capability -> allowed agents). Strategy Agent is
# intentionally excluded from live web access; it reasons over stored findings.
#
# "identity" is the bounded identity-resolution pre-phase (FIX_PRD_1 §6): one
# first-party fetch + merchant-aware verification searches. It is not a
# specialist agent; it gets no news/shopping/trends capabilities.
#
# Fleet PRD A2 additions:
#   "reviews"  — community sentiment (Reddit threads, YouTube reviews, social)
#   "ads"      — promotional/creative intelligence (social ads surfaces)
#   "catalog"  — deep product/pricing scan (merchant + competitor catalogues)
AGENT_CAPABILITIES: dict[str, set[str]] = {
    "market": {"web_search", "news_search", "trends_search", "youtube_search", "fetch_url"},
    "competitor": {"web_search", "news_search", "shopping_search", "fetch_url"},
    "buyer": {"web_search", "shopping_search", "fetch_url", "browser_extract"},
    "presence": {"web_search", "news_search", "reddit_search", "social_search", "fetch_url"},
    "strategy": set(),
    "identity": {"web_search", "fetch_url", "browser_extract"},
    "reviews": {"reddit_search", "youtube_search", "social_search", "news_search", "fetch_url"},
    "ads": {"social_search", "web_search", "news_search", "fetch_url"},
    "catalog": {"shopping_search", "web_search", "fetch_url", "browser_extract"},
    "scout": {"web_search", "news_search", "fetch_url"},
}
