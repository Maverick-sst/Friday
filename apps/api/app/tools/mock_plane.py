"""Deterministic mock tool plane (PRD_3 §37 Phase 11: safe fallback paths).

Used when COMPOSIO is unavailable/rate-limited and in tests, so the demo
degrades gracefully instead of crashing the fleet. Seeded with a realistic
corpus around the demo merchants (GearUp Cycles / Velocity Sports) so agent
output stays meaningful offline.
"""

from app.tools.base import FetchResult, SearchHit

_WEB = {
    "running shoes": [
        SearchHit(
            "https://runfit.in/best-running-shoes-2026",
            "Best Running Shoes 2026 (India)",
            "Beginner-friendly picks under INR 5,000: Nike Revolution 7, Asics "
            "Gel-Venture 9, Puma Flyer Runner. Delivery speed and returns "
            "dominate buyer complaints.",
        ),
        SearchHit(
            "https://gearguide.in/road-runner-guide",
            "Road Runner Buying Guide",
            "Comfort and delivery certainty outrank brand for first-time "
            "buyers; 68% of surveyed beginners filter by delivery date.",
        ),
    ],
    "cycling": [
        SearchHit(
            "https://cycleworld.in/india-market-report",
            "India Cycling Market Report 2026",
            "Premium road category growing 18% YoY; entry MTB segment "
            "consolidating around three national D2C brands. Accessories "
            "attach-rate rising on bundles.",
        ),
        SearchHit(
            "https://bikenews.daily/d2c-brands",
            "D2C Bike Brands to Watch",
            "Direct-to-consumer bicycle brands are winning via subscription "
            "maintenance plans and 48-hour metro delivery promises.",
        ),
    ],
}

_NEWS = {
    "cycling": [
        SearchHit(
            "https://bikenews.daily/metro-delivery-war",
            "Metro Delivery War Reaches Bicycles",
            "Two large D2C players now promise next-day assembly-and-delivery "
            "in top-8 metros, pressuring smaller stores.",
        ),
    ],
}

_SHOPPING = {
    "running shoes": [
        SearchHit(
            "https://shop.example.com/nike-revolution-7",
            "Nike Revolution 7 - INR 3,999",
            "Wide sizes available. Free delivery over INR 999. 4.2 stars (1,204 ratings).",
        ),
        SearchHit(
            "https://competitor-x.com/downshifter-14",
            "Competitor X Downshifter 14 - INR 4,299",
            "Explicit 'arrives by Thursday' badge, free 30-day returns, 4.5 stars (890 ratings).",
        ),
        SearchHit(
            "https://velocitysports.example.com/rev-7",
            "Velocity Sports Revolution 7 - INR 3,799",
            "'Fast shipping' claimed but no date shown; returns policy buried in footer.",
        ),
    ],
    "bicycle": [
        SearchHit(
            "https://competitor-x.com/roadster-pro",
            "Competitor X Roadster Pro - INR 24,999",
            "Free home assembly, 10-year frame warranty, EMI badges above the fold.",
        ),
        SearchHit(
            "https://gearupcycles.example.com/road-100",
            "GearUp Road 100 - INR 23,499",
            "Assembly extra INR 500; warranty details only in PDF spec sheet.",
        ),
    ],
}

_TRENDS = {
    "cycling": [
        SearchHit(
            "",
            "Trend signal: cycling India",
            "Search interest for 'road bike india' +22% QoQ; 'cycle repair "
            "near me' seasonal peak Oct-Dec; e-bike queries doubling annually.",
            source="trends",
        ),
    ],
}

# Source-scoped corpora (Fleet PRD A1): same demo-merchant universe so the
# reviews/ads/catalog agents produce meaningful output offline.
_REDDIT = {
    "running shoes": [
        SearchHit(
            "https://reddit.com/r/RunningShoeGeeks/comments/beginner-india",
            "r/RunningShoeGeeks: Best beginner shoes in India under 5k?",
            "Top comment: 'Downshifter 14 held up 8 months but sizing runs "
            "small.' Thread complains about vague delivery dates from two D2C "
            "stores; praise for free-returns policies.",
            source="reddit",
        ),
    ],
    "cycling": [
        SearchHit(
            "https://reddit.com/r/india_cycling/comments/d2c-assembly",
            "r/india_cycling: D2C bike assembly experience?",
            "Users report GearUp charges extra for assembly and warranty is "
            "PDF-only; Competitor X praised for free home assembly.",
            source="reddit",
        ),
    ],
}

_YOUTUBE = {
    "running shoes": [
        SearchHit(
            "https://youtube.com/watch?v=beginner-shoes-inr5000",
            "Best Running Shoes Under ₹5,000 — 2026 India Test",
            "Reviewer ranks Competitor X first for delivery certainty; "
            "Velocity Sports 'good value but returns process is unclear'.",
            source="youtube",
        ),
    ],
    "cycling": [
        SearchHit(
            "https://youtube.com/watch?v=road100-review",
            "GearUp Road 100 — 3-Month Ownership Review",
            "Frame quality praised; assembly extra cost flagged at 4:12; "
            "warranty claim process described as 'slow but honest'.",
            source="youtube",
        ),
    ],
}

_SOCIAL = {
    "running shoes": [
        SearchHit(
            "https://instagram.com/p/competitor-x-monsoon-sale",
            "Competitor X on Instagram: Monsoon Sale — flat 20% off",
            "High engagement promo post; 'arrives by Thursday' messaging in "
            "caption; countdown sticker drives urgency.",
            source="social",
        ),
        SearchHit(
            "https://facebook.com/ads/library/velocitysports",
            "Facebook Ad Library: Velocity Sports active ads",
            "3 active ads, all generic 'fast shipping' creative; no price or "
            "delivery-date specifics; last creative update 6 weeks ago.",
            source="social",
        ),
    ],
    "cycling": [
        SearchHit(
            "https://instagram.com/p/gearup-monsoon-ride",
            "GearUp Cycles on Instagram: Monsoon group ride recap",
            "Community-building content, low promo pressure; comments ask "
            "about EMI options — unanswered.",
            source="social",
        ),
    ],
}


_PAGES = {
    "https://competitor-x.com": (
        "Competitor X - Home | Free 30-day returns on everything. Arrives by "
        "Thursday when you order today. 4.5-star average across 12k reviews. "
        "Delivery certainty messaging appears site-wide above every product grid."
    ),
    "https://velocitysports.example.com": (
        "Velocity Sports | Fast shipping on all orders! (no dates). Returns "
        "policy link in footer. Product pages list price and stock but no "
        "delivery estimates or review snippets."
    ),
    "https://gearupcycles.example.com": (
        "GearUp Cycles | Performance bicycles built for Indian roads. Free "
        "fitting at Bangalore store. Assembly service INR 500. Warranty info "
        "in PDF."
    ),
}


class MockToolPlane:
    """Deterministic offline plane: same interface as ComposioToolPlane."""

    @property
    def name(self) -> str:
        return "mock"

    def _match(self, corpus: dict[str, list[SearchHit]], query: str) -> list[SearchHit]:
        q = query.lower()
        best_key, best_score = None, 0
        for key in corpus:
            score = sum(w in q for w in key.split())
            if score > best_score:
                best_key, best_score = key, score
        if best_key is None:
            generic_title = f"Result: {query[:60]}"
            return [
                SearchHit(
                    f"https://web.example/search?q={query.replace(' ', '+')}",
                    generic_title,
                    "Generic offline result (mock plane).",
                )
            ]
        return corpus[best_key][:5]

    async def search_web(self, query: str) -> list[SearchHit]:
        return self._match(_WEB, query)

    async def search_news(self, query: str) -> list[SearchHit]:
        return self._match({**_WEB, **_NEWS}, query)

    async def search_shopping(self, query: str) -> list[SearchHit]:
        return self._match(_SHOPPING, query)

    async def search_trends(self, query: str) -> list[SearchHit]:
        return self._match(_TRENDS, query)

    async def search_reddit(self, query: str) -> list[SearchHit]:
        return self._match(_REDDIT, query)

    async def search_youtube(self, query: str) -> list[SearchHit]:
        return self._match(_YOUTUBE, query)

    async def search_social(self, query: str) -> list[SearchHit]:
        return self._match(_SOCIAL, query)

    async def fetch_url(self, urls: list[str], max_chars: int = 6000) -> list[FetchResult]:
        out = []
        for url in urls:
            text = None
            for prefix, page in _PAGES.items():
                if url.startswith(prefix):
                    text = page
                    break
            if text is None:
                text = f"Content of {url} could not be retrieved (mock plane)."
            out.append(FetchResult(url=url, text=text[:max_chars], truncated=len(text) > max_chars))
        return out

    async def browser_extract(self, url: str, prompt: str | None = None, on_started=None) -> dict | None:
        # No browser backend in the deterministic mock plane (Fleet PRD B6).
        return None
