"""Deterministic ScriptedBrain (zero-API-key demo path).

Parses nothing financial: it only picks *what* to inspect next. Every money
decision flows through the gateway's deterministic policy engine.
"""

import re

from app.agent.base import AgentEvent

_STOPWORDS = {
    "find",
    "me",
    "buy",
    "get",
    "the",
    "a",
    "an",
    "for",
    "under",
    "below",
    "with",
    "good",
    "best",
    "reliable",
    "return",
    "returns",
    "policy",
    "please",
    "i",
    "want",
    "and",
    "inr",
    "rs",
    "budget",
    "size",
    "my",
    "of",
    "to",
    "it",
}

MAX_STEPS = 6


def _query_from_intent(intent: str) -> str:
    text = intent.lower()
    text = re.sub(r"size\s*\d+", " ", text)
    text = re.sub(r"(under|below|<)\s*(inr|rs\.?|\u20b9)?\s*[\d,]+", " ", text)
    tokens = [t for t in re.findall(r"[a-z0-9]+", text) if t not in _STOPWORDS and len(t) > 1]
    return " ".join(tokens[:6])


def _preferred_size(intent: str, constraints: dict) -> str | None:
    if constraints.get("preferred_size"):
        return str(constraints["preferred_size"])
    match = re.search(r"size\s*(\d+)", intent.lower())
    return match.group(1) if match else None


def _last_tool_result(history: list[AgentEvent]) -> dict:
    for event in reversed(history):
        if event.type == "tool_result":
            payload = event.payload or {}
            # Runner wraps tool output under "result"; errors surface as dicts too.
            inner = payload.get("result")
            return inner if isinstance(inner, dict) else {}
    return {}


def _pick_product(search_result: dict, query: str) -> dict | None:
    products = search_result.get("products") or []
    if not products:
        return None
    q_tokens = set(query.lower().split())

    def score(p: dict) -> tuple[int, str]:
        hay = f"{p.get('title', '')} {p.get('brand', '')} {p.get('category', '')}".lower()
        hits = sum(1 for t in q_tokens if t in hay)
        return (-hits, p.get("title", ""))

    ranked = sorted(products, key=score)
    return ranked[0]


def _pick_variant(product: dict, size: str | None) -> dict | None:
    variants = product.get("variants") or []
    available = [v for v in variants if v.get("available")]
    if not available:
        return None
    if size:
        for v in available:
            opts = {str(k).lower(): str(val).lower() for k, val in (v.get("options") or {}).items()}
            if opts.get("size") == str(size).lower():
                return v
    return available[0]


class ScriptedBrain:
    """Plan: discover -> search -> quote chosen variant -> cart -> checkout."""

    def __init__(self, intent: str, constraints: dict):
        self.intent = intent
        self.constraints = constraints or {}
        self._step = 0

    def next_action(self, history: list[AgentEvent]) -> tuple[str, dict]:
        self._step += 1
        if self._step > MAX_STEPS:
            raise RuntimeError("ScriptedBrain exceeded plan length")

        last = _last_tool_result(history)

        if self._step == 1:
            return "discover_merchant", {}

        if self._step == 2:
            return "search_products", {"query": _query_from_intent(self.intent)}

        if self._step == 3:
            query = _query_from_intent(self.intent)
            product = _pick_product(last, query)
            if product is None:
                raise LookupError("No matching product found in the catalog")
            size = _preferred_size(self.intent, self.constraints)
            variant = _pick_variant(product, size)
            if variant is None:
                label = f" size {size}" if size else ""
                raise LookupError(f"No available{label} variant for {product.get('title')}")
            self.constraints["_selection"] = {
                "title": product.get("title"),
                "option": variant.get("options"),
                "listed_price_minor": variant.get("price_minor"),
            }
            return "get_quote", {
                "product_id": product["product_id"],
                "variant_id": variant["variant_id"],
                "quantity": 1,
            }

        if self._step == 4:
            if not last.get("quote_id"):
                raise LookupError("Quote step did not produce a usable quote")
            return "create_cart", {"quote_id": last["quote_id"]}

        if self._step == 5:
            if not last.get("quote_id"):
                raise LookupError("Cart step lost the quote reference")
            return "checkout", {"quote_id": last["quote_id"]}

        raise RuntimeError("ScriptedBrain ran out of plan")

    def selection_summary(self) -> dict:
        return self.constraints.get("_selection", {})
