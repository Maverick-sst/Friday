"""Live-web catalog materializer (Fleet PRD B1b).

Bridges the research plane to the transactable gateway: discovers REAL product
pages on the merchant's real domain (via the agent tool plane), extracts the
buyable offer from each page (JSON-LD/heuristics, LLM fallback), and
materializes them into the canonical products/product_variants tables with
source="live_web". From that point the standard gateway flow (search -> quote
-> policy -> checkout) works unchanged, and live_validate_variant re-fetches
the real page at transaction time.

Also flips the merchant's integration provider to "web_live" — but only when
no integration exists yet (never stomps a working mock/shopify integration).
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings

logger = logging.getLogger("acg.intel.web_catalog")

_MAX_PRODUCTS = 3
_MAX_OOS_STREAK = 2  # stop after N pages in a row with no buyable offer


class ExtractedOffer(BaseModel):
    """LLM fallback extraction schema for one product page."""

    title: str = ""
    price_minor: int | None = None
    currency: str = "INR"
    available_for_sale: bool = False
    confidence: float = 0.5


@dataclass(slots=True)
class MaterializedProduct:
    title: str
    url: str
    price_minor: int
    currency: str
    available_for_sale: bool
    confidence: float
    method: str


@dataclass(slots=True)
class MaterializationResult:
    products: list[MaterializedProduct] = field(default_factory=list)
    untransactable_reasons: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    pages_failed: int = 0

    @property
    def transactable(self) -> bool:
        return bool(self.products)


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _candidate_urls(shopping_hits, web_hits, merchant_domain: str) -> list[str]:
    """Product-page URLs, merchant-domain first (strict), then shopping hits."""
    seen: set[str] = []
    for hit in [*web_hits, *shopping_hits]:
        url = (hit.url or "").strip()
        if not url or "://" not in url or url in seen:
            continue
        if _domain_of(url) == merchant_domain:
            seen.append(url)
    if seen:
        return seen
    # No same-domain hit from search: the storefront's own domain is the only
    # guaranteed in-domain origin, but we have no deep product links for it.
    return []


async def _extract_offer(llm, url: str, page_text: str) -> dict | None:
    """Heuristics first, structured LLM fallback. None = untransactable page."""
    from app.adapters.commerce.web_live import WebLiveExtractionError, extract_offer_from_page

    try:
        return extract_offer_from_page(url, page_text)
    except WebLiveExtractionError:
        pass
    if llm is None:
        return None
    try:
        offer, _raw = await llm.structured_generate(
            [
                {
                    "role": "user",
                    "content": (
                        "Extract the single buyable offer from this e-commerce product page. "
                        "price_minor = price in paise (₹1 = 100). "
                        "available_for_sale = purchasable right now. "
                        "confidence = 0-1 how sure you are. If there is no buyable offer, "
                        "leave price_minor null.\n\n"
                        f"URL: {url}\n\nPAGE:\n{page_text[:6000]}"
                    ),
                }
            ],
            ExtractedOffer,
        )
    except Exception as exc:
        logger.warning("llm offer extraction failed for %s: %s", url[:120], exc)
        return None
    if not offer.price_minor or offer.price_minor <= 0:
        return None
    return {
        "price_minor": int(offer.price_minor),
        "currency": offer.currency or "INR",
        "available_for_sale": bool(offer.available_for_sale),
        "confidence": float(offer.confidence or 0.5),
        "method": "llm",
        "title": offer.title,
    }


async def materialize_live_catalog(
    db,
    merchant,
    tools,  # ToolRouter: budget + provenance for every call
    llm=None,
    query: str | None = None,
    domain: str | None = None,
    force_browser: bool = False,
) -> MaterializationResult:
    """Discover + materialize REAL product offers from the merchant's website.

    Consumes the buyer run's tool budget (every call is an audited observation).
    Returns what became transactable, and honest reasons when nothing did.

    `force_browser=True` (shopping mission): the user gave a concrete product
    spec, so a broad materialization is wasteful — go straight to the page via
    the managed stealth browser (Browser Use) which renders past bot-walls/JS
    shells. Static fetch is skipped in favor of the browser when set.
    """
    result = MaterializationResult()
    website = getattr(merchant, "website_url", "") or ""
    domain = domain or _domain_of(website)
    if not domain:
        result.untransactable_reasons.append("merchant has no website domain to search")
        return result

    q = (query or "").strip() or f"{getattr(merchant, 'name', '')} buy online"

    shopping_obs = await tools.search_shopping(q)
    web_obs = await tools.search_web(f"site:{domain} {q} price buy online")
    candidates = _candidate_urls(shopping_obs.hits, web_obs.hits, domain)
    if not candidates:
        # One broader attempt before declaring untransactable.
        web_obs = await tools.search_web(f"{getattr(merchant, 'name', '')} {q} price")
        candidates = _candidate_urls(shopping_obs.hits, web_obs.hits, domain)
    if not candidates:
        result.untransactable_reasons.append(
            f"no product pages found on {domain} via shopping/web search"
        )
        return result

    oos_streak = 0
    browser_pages_left = get_settings().browser_use_max_pages_per_run
    for url in candidates[:_MAX_PRODUCTS]:
        if oos_streak >= _MAX_OOS_STREAK:
            break
        offer = None
        # Shopping mission: browser first (renders past bot-walls/JS shells).
        if force_browser and browser_pages_left > 0:
            browser_pages_left -= 1
            offer, _browser_run_id = await tools.browser_extract(url)
        if offer is None:
            fetches = await tools.fetch_url([url])
            if not fetches or not fetches[0].text:
                result.pages_failed += 1
                result.untransactable_reasons.append(f"page fetch failed: {url[:120]}")
                continue
            result.pages_fetched += 1
            offer = await _extract_offer(llm, url, fetches[0].text)
        if offer is None and not force_browser and browser_pages_left > 0:
            # B6 escalation: static fetch produced no machine-readable offer (anti-bot
            # wall, JS-only rendering) — try the managed stealth browser, bounded by
            # the per-run credit cap.
            browser_pages_left -= 1
            offer, _browser_run_id = await tools.browser_extract(url)
        if offer is None:
            result.pages_failed += 1
            oos_streak += 1
            result.untransactable_reasons.append(f"no machine-readable offer on: {url[:120]}")
            continue
        oos_streak = 0
        await _upsert_product(db, getattr(merchant, "id", ""), url, offer)
        result.products.append(
            MaterializedProduct(
                title=offer.get("title") or "Product",
                url=url,
                price_minor=offer["price_minor"],
                currency=offer.get("currency") or "INR",
                available_for_sale=bool(offer["available_for_sale"]),
                confidence=float(offer.get("confidence") or 0.5),
                method=offer.get("method") or "heuristic",
            )
        )

    if result.products:
        await _ensure_web_live_integration(db, getattr(merchant, "id", ""), website)
    else:
        result.untransactable_reasons.append(
            "no offer could be extracted — merchant is not AI-transactable in current state"
        )
    await db.commit()
    return result


async def _upsert_product(db, merchant_id: str, url: str, offer: dict) -> None:
    from app.db.models import Product, ProductVariant

    row = await db.scalar(
        select(Product).where(Product.merchant_id == merchant_id, Product.external_id == url)
    )
    if row is None:
        row = Product(merchant_id=merchant_id, source="live_web", external_id=url)
        db.add(row)
    # Assign every column BEFORE flushing: flush() emits the INSERT, and
    # products.title is NOT NULL — a pre-assignment flush ships title=NULL
    # and violates the constraint (seen live on adidas.co.in).
    row.title = (offer.get("title") or row.title or "Live product")[:255]
    row.product_url = url
    row.category = row.category or None
    await db.flush()

    variant = await db.scalar(select(ProductVariant).where(ProductVariant.external_id == url))
    if variant is None:
        variant = ProductVariant(product_id=row.id, currency=offer.get("currency") or "INR")
        db.add(variant)
    variant.external_id = url
    variant.title = "live-web offer"
    variant.options_json = {"source": "live_web", "url": url}
    variant.price = offer["price_minor"]
    variant.currency = offer.get("currency") or "INR"
    variant.available_for_sale = bool(offer["available_for_sale"])
    variant.available_quantity = 1 if offer["available_for_sale"] else 0
    variant.sku = f"WEB-{abs(hash(url)) % 10**8:08d}"
    await db.flush()


async def _ensure_web_live_integration(db, merchant_id: str, website_url: str) -> None:
    """Point the gateway at the live-web adapter — without stomping others."""
    from app.db.models import MerchantIntegration

    integration = await db.scalar(
        select(MerchantIntegration).where(MerchantIntegration.merchant_id == merchant_id)
    )
    if integration is None:
        db.add(
            MerchantIntegration(
                merchant_id=merchant_id,
                provider="web_live",
                store_url=website_url,
                status="active",
            )
        )
    # An existing integration (mock/shopify) is intentionally left untouched:
    # those merchants keep their own adapters and validation semantics.