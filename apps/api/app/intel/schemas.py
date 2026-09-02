"""Structured output schemas each specialist must return through its LLM."""

from typing import Literal

from pydantic import BaseModel, Field


class ClaimOut(BaseModel):
    """One evidence-backed claim observed during research."""

    claim: str
    source_url: str | None = None
    epistemic_state: Literal["fact", "inference", "speculation"] = "fact"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # FIX_PRD_1 §11/§12: relevance to THIS merchant is an independent dimension
    # from factual confidence. None = unscored (legacy outputs; promoted as-is
    # with a relevance_unscored marker so old behavior stays backward-compatible).
    entity_relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    excerpt: str | None = None


class FindingOut(BaseModel):
    title: str
    statement: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_indexes: list[int] = []  # 0-based refs into the claims array
    tags: list[str] = []


class ResearchOutput(BaseModel):
    """Common shape for research agents (market/competitor/presence)."""

    summary: str
    claims: list[ClaimOut] = []
    findings: list[FindingOut] = []
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class BuyerCandidate(BaseModel):
    name: str
    merchant_or_url: str | None = None
    price_note: str | None = None
    trust_signals: str | None = None
    friction: str | None = None


class BuyerSimulationOutput(BaseModel):
    """PRD_3 §7.3 required output for AI buyer simulations."""

    summary: str
    persona_used: str
    candidates: list[BuyerCandidate] = []
    selected: str | None = None  # candidate name or null if none chosen
    ranking: list[str] = []
    rejection_reasons: dict[str, str] = {}
    friction_observed: list[str] = []
    claims: list[ClaimOut] = []
    findings: list[FindingOut] = []
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Fleet PRD B3: factual transaction outcome, attached by the agent AFTER
    # generation (never authored by the LLM — the gateway is the source of truth).
    transaction: dict | None = None


class RecommendationOut(BaseModel):
    problem: str
    why_it_matters: str
    recommendation_text: str
    expected_impact: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    impact: Literal["low", "medium", "high"] = "medium"
    finding_titles: list[str] = []
    suggested_next_mission: str | None = None


class StrategySynthesisOutput(BaseModel):
    summary: str
    recommendations: list[RecommendationOut] = []
    conflicting_signals: list[str] = []
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


# --- Merchant identity resolution (FIX_PRD_1 §5/§6) --------------------------


class MerchantIdentityPacket(BaseModel):
    """Canonical merchant identity resolved before specialist research.

    Produced from first-party inspection of the supplied URL plus bounded
    external verification; persisted additively on
    `merchant_profiles.extra_json["identity_packet"]` (no schema migration).
    Never hard-coded per merchant.
    """

    canonical_name: str
    domain: str | None = None
    canonical_url: str | None = None
    business_type: str | None = None
    primary_category: str | None = None
    geography: str | None = None
    description: str | None = None
    known_product_types: list[str] = []
    official_domains: list[str] = []
    identity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class IdentityResolutionOutput(BaseModel):
    """What the identity-resolution LLM call must return (FIX_PRD_1 §6)."""

    canonical_name: str
    business_type: str | None = None
    primary_category: str | None = None
    geography: str | None = None
    description: str | None = None
    known_product_types: list[str] = []
    official_domains: list[str] = []
    identity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_notes: list[str] = []  # detected same-name / homonym collisions
