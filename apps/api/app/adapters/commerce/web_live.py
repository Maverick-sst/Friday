"""Live-web commerce adapter (Fleet PRD B1): abstracts an arbitrary real
storefront into the canonical commerce model at transaction time.

No Shopify integration required. The merchant's real product page URL is the
source of truth:

- `external_variant_id` == the product page URL (one buyable offer per page)
- `sync_catalog` is a no-op: materialization happens in the async bridge
  (app/intel/web_catalog.py) which has LLM + tool-plane access
- `live_validate_variant` RE-FETCHES the page and re-derives price/availability
  (PRD §16 stale-data boundary, for real). If the page cannot be fetched or no
  machine-readable price exists, it raises LookupError -> the gateway blocks
  deterministically. That failure IS the AI-transactability signal.
"""

import asyncio
import json
import logging
import re
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.adapters.commerce.base import (
    LiveVariantState,
    MerchantMetadata,
    SourceOrderResult,
    SyncResult,
)
from app.adapters.demo_support import apply_demo_override

logger = logging.getLogger("acg.adapters.web_live")

_FETCH_TIMEOUT = 20.0
_UA = "Mozilla/5.0 (compatible; ACGBuyerAgent/1.0; +https://agent-commerce.test/bot)"

# Price heuristics: INR-majority market, but tolerate plain JSON numbers.
_PRICE_PATTERNS = [
    re.compile(r'"price"\s*:\s*"?([\d][\d,]*(?:\.\d+)?)"?'),
    re.compile(r"[₹]\s*([\d][\d,]*(?:\.\d+)?)"),
    re.compile(r"\b(?:Rs\.?|INR)\s*([\d][\d,]*(?:\.\d+)?)", re.IGNORECASE),
]
_OOS_PATTERNS = re.compile(
    r"(sold\s*out|out\s+of\s+stock|unavailable|notify\s+me|discontinued)", re.IGNORECASE
)
_IN_STOCK_PATTERNS = re.compile(r"(add\s+to\s+(cart|bag)|buy\s+now|in\s+stock)", re.IGNORECASE)


class WebLiveExtractionError(Exception):
    """No machine-derivable offer could be extracted from the live page."""


def _parse_number(raw: str) -> int | None:
    digits = re.sub(r"[,\s]", "", raw)
    try:
        value = float(digits)
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(round(value * 100))  # rupees -> minor units


def _extract_title(html: str) -> str:
    """Best-effort product name from page metadata (og:title -> h1 -> <title>).

    The gateway session searches the MATERIALIZED catalog by title/brand/
    category; without a real name (adidas-style heuristic pages), every search
    missed and the buyer stopped right after discovery.
    """
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{2,300})["\']',
        r'<meta[^>]+content=["\']([^"\']{2,300})["\'][^>]+property=["\']og:title["\']',
        r"<h1[^>]*>(.*?)</h1>",
        r"<title[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, html[:60_000], re.IGNORECASE | re.DOTALL)
        if match:
            text = re.sub(r"<[^>]+>", " ", match.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= 2:
                return text[:200]
    return ""


def extract_offer_from_page(url: str, text: str) -> dict[str, Any]:
    """Derive one buyable offer (price/availability) from a product page.

    Order: JSON-LD Product schema (most reliable, shipped by most real
    storefronts) -> price heuristics + availability keyword signals.
    Returns {price_minor, currency, available_for_sale, confidence, method,
    title} or raises WebLiveExtractionError.
    """
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            if not isinstance(node, dict) or "Product" not in str(node.get("@type", "")):
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_raw = offers.get("price") or offers.get("lowPrice")
            if price_raw is not None:
                price_minor = _parse_number(str(price_raw))
                if price_minor:
                    availability = str(offers.get("availability", ""))
                    # Schema.org canonical value is "InStock" (often as the full
                    # IRI https://schema.org/InStock); compare case-insensitively
                    # so canonical in-stock pages aren't materialized as sold out.
                    availability_l = availability.lower()
                    return {
                        "price_minor": price_minor,
                        "currency": str(offers.get("priceCurrency") or "INR"),
                        "available_for_sale": ("instock" in availability_l) or (not availability_l),
                        "confidence": 0.9,
                        "method": "jsonld",
                        "title": (str(node.get("name") or "").strip() or _extract_title(text))[:200],
                    }
    best: int | None = None
    for pattern in _PRICE_PATTERNS:
        for m in pattern.finditer(text[:120_000]):
            value = _parse_number(m.group(1))
            # Sanity window: below ₹50 / above ₹10,00,000 is noise (specs, ids).
            if value and 5_000 <= value <= 100_000_000:
                best = value if best is None else min(best, value)
        if best is not None:
            break
    if best is None:
        raise WebLiveExtractionError(f"no machine-readable price on {url}")
    oos = bool(_OOS_PATTERNS.search(text[:60_000]))
    in_stock = bool(_IN_STOCK_PATTERNS.search(text[:60_000]))
    return {
        "price_minor": best,
        "currency": "INR",
        "available_for_sale": in_stock and not oos,
        "confidence": 0.6,
        "method": "heuristic",
        "title": _extract_title(text),
    }


class WebLiveAdapter:
    """CommerceAdapter for real websites abstracted at transaction time."""

    provider = "web_live"

    def __init__(self, fetcher: Callable[[str], str] | None = None) -> None:
        # Injectable for deterministic tests; default = real HTTP GET.
        self._fetcher = fetcher

    def _fetch(self, url: str) -> str:
        if self._fetcher is not None:
            return self._fetcher(url)
        resp = httpx.get(
            url,
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9"},
        )
        if resp.status_code >= 400:
            raise LookupError(f"live page fetch failed: http {resp.status_code} for {url}")
        return resp.text

    # -- CommerceAdapter surface -------------------------------------------------

    def fetch_merchant_metadata(self, store_ref: str) -> MerchantMetadata:
        domain = urlparse(store_ref if "://" in store_ref else f"https://{store_ref}").netloc
        name = (domain.split(".")[0] if domain else "web merchant").replace("-", " ").title()
        return MerchantMetadata(
            name=name,
            slug=domain or "web-merchant",
            description=f"Live-web merchant abstracted from {store_ref}",
            category=None,
            website_url=store_ref,
            logo_url=None,
            currency="INR",
        )

    def sync_catalog(self, db: Session, merchant_id: str) -> SyncResult:
        # Materialization is performed by the async bridge (intel/web_catalog.py)
        # which has tool-plane + LLM access; this adapter owns validation only.
        return SyncResult()

    def live_validate_variant(
        self, db: Session, merchant_id: str, variant_external_id: str
    ) -> LiveVariantState:
        url = variant_external_id
        if "://" not in url:
            raise LookupError(f"web_live variant id is not a URL: {url[:120]}")
        try:
            text = self._fetch(url)
        except LookupError:
            raise
        except Exception as exc:
            raise LookupError(f"live web state unavailable for {url[:120]}: {exc}") from exc

        try:
            offer = extract_offer_from_page(url, text)
        except WebLiveExtractionError:
            offer = self._llm_fallback(url, text)

        state = LiveVariantState(
            external_variant_id=url,
            external_product_id=url,
            price_minor=offer["price_minor"],
            currency=offer.get("currency") or "INR",
            available_for_sale=bool(offer["available_for_sale"]),
            available_quantity=None,
            raw={
                "provider": "web_live",
                "url": url,
                "method": offer.get("method"),
                "confidence": offer.get("confidence"),
            },
        )
        return apply_demo_override(db, merchant_id, url, state)

    def _llm_fallback(self, url: str, text: str) -> dict[str, Any]:
        """Structured LLM extraction when heuristics fail (sync ctx, async provider)."""
        try:
            from pydantic import BaseModel

            from app.llm.factory import get_llm_provider

            class Offer(BaseModel):
                price_minor: int | None = None
                currency: str = "INR"
                available_for_sale: bool = False

            provider = get_llm_provider()
            page = text[:6000]
            messages = [
                {
                    "role": "user",
                    "content": (
                        "Extract the single buyable offer from this product page. "
                        "price_minor = price in paise (₹1 = 100). "
                        "available_for_sale = can a customer buy it right now? "
                        "If no buyable offer exists, leave price_minor null.\n\n"
                        f"URL: {url}\n\nPAGE:\n{page}"
                    ),
                }
            ]
            result, _raw = asyncio.run(provider.structured_generate(messages, Offer))
            if result.price_minor and result.price_minor > 0:
                return {
                    "price_minor": result.price_minor,
                    "currency": result.currency or "INR",
                    "available_for_sale": result.available_for_sale,
                    "confidence": 0.7,
                    "method": "llm",
                    "title": "",
                }
        except Exception as exc:  # never crash the gateway on LLM trouble
            logger.warning("web_live llm extraction fallback failed: %s", exc)
        raise WebLiveExtractionError(f"no machine-readable price on {url}")

    def create_source_order(self, db: Session, merchant_id: str, order: dict[str, Any]) -> SourceOrderResult:
        # No merchant-side API to push to; the platform's transaction record IS
        # the order context for live-web merchants.
        return SourceOrderResult(
            reference=f"web-live-{order.get('txn_ref', 'order')}", raw={"status": "recorded"}
        )