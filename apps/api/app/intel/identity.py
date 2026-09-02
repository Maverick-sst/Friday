"""Merchant identity resolution (FIX_PRD_1 §5-§8).

Bounded pre-phase before any specialist research:

    URL -> first-party fetch -> merchant-aware verification search
         -> one structured LLM synthesis -> MerchantIdentityPacket
         -> confidence gate (degraded-but-honest on low confidence)

Seams: _llm/_plane delegate to app.intel.handlers at call time, so the
documented test monkeypatch points control this module too. The resolver
never raises into the baseline: any failure yields a deterministic URL-derived
fallback packet whose low confidence puts research into degraded
(domain-anchored) mode instead of fabricating certainty.
"""

import asyncio
import logging
from urllib.parse import urlparse

from app.core.config import get_settings
from app.engine.context import RunBudget
from app.intel.prompts import IDENTITY_SYSTEM
from app.intel.schemas import IdentityResolutionOutput, MerchantIdentityPacket
from app.tools.router import ToolRouter

logger = logging.getLogger("acg.intel.identity")


def _llm():
    from app.intel.handlers import _get_llm

    return _get_llm()


def _plane():
    from app.intel.handlers import _get_plane

    return _get_plane()


def domain_of(url: str | None) -> str:
    """Hostname without a leading www. — '' when empty/unparsable."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def brand_token_from_url(url: str) -> str:
    host = domain_of(url)
    if not host:
        return "unknown"
    return host.split(".")[0] or "unknown"


def _fallback_packet(url: str) -> MerchantIdentityPacket:
    """Deterministic URL-derived packet; low confidence -> degraded mode."""
    domain = domain_of(url)
    token = brand_token_from_url(url)
    return MerchantIdentityPacket(
        canonical_name=token.replace("-", " ").strip().title() or "Unknown",
        domain=domain or None,
        canonical_url=url,
        business_type=None,
        primary_category=None,
        geography=None,
        description=None,
        known_product_types=[],
        official_domains=[domain] if domain else [],
        identity_confidence=0.4,
    )


async def resolve_merchant_identity(
    *,
    mission_id: str,
    merchant_id: str,
    url: str,
    merchant_name: str,
    goal: str | None = None,
) -> tuple[MerchantIdentityPacket, list, dict]:
    """One bounded identity resolution. Returns (packet, observations, meta).

    Tool calls go through ToolRouter (budget-metered via the "identity"
    capability row, observations logged to the SSE feed and persisted as
    evidence by the caller). This function itself never raises.
    """
    settings = get_settings()
    router = ToolRouter(
        _plane(),
        agent_key="identity",
        mission_id=mission_id,
        budget=RunBudget(max_tool_calls=settings.identity_tool_budget),
    )

    brand = brand_token_from_url(url)
    page_text = ""
    try:
        pages = await router.fetch_url([url])
        if pages and pages[0].text:
            page_text = pages[0].text[:3000]
    except Exception as exc:  # tool failures are observations, never crashes
        logger.warning("identity first-party fetch failed: %s", exc)

    verification_hits = []
    try:
        # Merchant-aware, not name-only (FIX_PRD_1 §7): quoted brand + intent.
        # ToolRouter.search_* returns a ToolObservation wrapper; the hits live
        # on .hits (and .ok is False when the call failed).
        verification_obs = await router.search_web(f'"{brand}" brand online store')
        verification_hits = list(verification_obs.hits) if verification_obs.ok else []
    except Exception as exc:
        logger.warning("identity verification search failed: %s", exc)

    observations = list(router.observations)
    meta: dict = {
        "first_party_chars": len(page_text),
        "verification_hits": len(verification_hits),
        "ambiguity_notes": [],
    }

    verification_block = (
        "\n".join(
            f"- {h.url or 'no-url'} | {h.title}: {h.snippet[:200]}" for h in verification_hits[:6]
        )
        or "(no verification results)"
    )
    try:
        result, _raw = await asyncio.wait_for(
            _llm().structured_generate(
                [
                    {"role": "system", "content": IDENTITY_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"SUPPLIED URL\n{url}\n\n"
                            f"MERCHANT NAME FROM ONBOARDING\n{merchant_name}\n\n"
                            f"MERCHANT GOAL\n{goal or 'not stated'}\n\n"
                            "FIRST-PARTY PAGE CONTENT (truncated)\n"
                            f"{page_text or '(fetch failed or empty)'}\n\n"
                            f"VERIFICATION SEARCH RESULTS\n{verification_block}\n\n"
                            "Resolve the canonical merchant identity now."
                        ),
                    },
                ],
                IdentityResolutionOutput,
            ),
            timeout=float(settings.identity_timeout_seconds),
        )
    except Exception as exc:
        logger.warning("identity LLM synthesis failed (%s); using URL-derived fallback", exc)
        meta["fallback"] = str(exc)[:200]
        return _fallback_packet(url), observations, meta

    if not isinstance(result, IdentityResolutionOutput):
        # Wrong schema shape (e.g. generic test fakes): honest degraded fallback.
        meta["fallback"] = "wrong schema from llm"
        return _fallback_packet(url), observations, meta

    domain = domain_of(url)
    official = {str(d).lower() for d in result.official_domains if d}
    if domain:
        official.add(domain)  # first-party truth: the supplied domain is official
    packet = MerchantIdentityPacket(
        canonical_name=result.canonical_name or brand.title(),
        domain=domain or None,
        canonical_url=url,
        business_type=result.business_type,
        primary_category=result.primary_category,
        geography=result.geography,
        description=result.description,
        known_product_types=result.known_product_types[:10],
        official_domains=sorted(official)[:10],
        identity_confidence=result.identity_confidence,
    )
    meta["ambiguity_notes"] = list(result.ambiguity_notes)[:5]
    return packet, observations, meta
