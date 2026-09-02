"""SQLAlchemy ORM models (PRD §19).

Portability notes for V0:
- Primary keys are String(36) UUIDs so tests can run on SQLite.
- JSON columns use JSONB on PostgreSQL, plain JSON elsewhere.
- All monetary amounts are BigInteger minor units (paise).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.base import new_uuid

JSONVariant = postgresql.JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Merchant(Base, TimestampMixin):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(120))
    subcategory: Mapped[str | None] = mapped_column(String(120))
    website_url: Mapped[str | None] = mapped_column(String(512))
    logo_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    agent_endpoint: Mapped[str | None] = mapped_column(String(512))


class MerchantIntegration(Base, TimestampMixin):
    __tablename__ = "merchant_integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    store_url: Mapped[str | None] = mapped_column(String(512))
    auth_reference_encrypted: Mapped[str | None] = mapped_column(Text)
    scopes_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("merchant_id", "provider", name="uq_merchant_provider"),)


class MerchantCapability(Base, TimestampMixin):
    __tablename__ = "merchant_capabilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability_name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (UniqueConstraint("merchant_id", "capability_name", name="uq_merchant_capability"),)


class MerchantPolicy(Base, TimestampMixin):
    __tablename__ = "merchant_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    max_auto_purchase: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    approval_threshold: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    allowed_categories_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    allowed_regions_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    return_window_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    allow_cancellation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    brand: Mapped[str | None] = mapped_column(String(120), index=True)
    product_url: Mapped[str | None] = mapped_column(String(512))
    image_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="shopify", nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base, TimestampMixin):
    __tablename__ = "product_variants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    sku: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    available_quantity: Mapped[int | None] = mapped_column(Integer)
    available_for_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped["Product"] = relationship(back_populates="variants")


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    buyer_id: Mapped[str | None] = mapped_column(String(120))
    merchant_id: Mapped[str | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), index=True
    )
    user_intent: Mapped[str | None] = mapped_column(Text)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    quote_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    variant_id: Mapped[str] = mapped_column(ForeignKey("product_variants.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    subtotal: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shipping_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    inventory_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    live_validated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="shopify", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Cart(Base, TimestampMixin):
    __tablename__ = "carts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cart_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_cart_id: Mapped[str | None] = mapped_column(String(120))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"))
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    txn_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"))
    variant_id: Mapped[str | None] = mapped_column(ForeignKey("product_variants.id"))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"))
    cart_id: Mapped[str | None] = mapped_column(ForeignKey("carts.id"))

    requested_amount: Mapped[int | None] = mapped_column(BigInteger)
    quoted_amount: Mapped[int | None] = mapped_column(BigInteger)
    authorized_amount: Mapped[int | None] = mapped_column(BigInteger)
    final_amount: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", nullable=False, index=True)

    shopify_reference: Mapped[str | None] = mapped_column(String(255))
    razorpay_order_id: Mapped[str | None] = mapped_column(String(120))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (Index("ix_transactions_session_status", "session_id", "status"),)


class TransactionEvent(Base):
    __tablename__ = "transaction_events"

    # INTEGER on sqlite so rowid autoincrement works; BigInteger elsewhere.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)


class IdempotencyKey(Base):
    """Idempotency guard for checkout / payment finalization (PRD §28)."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ============================================================================
# Strategy-team engine (PRD_3): merchants-as-workspaces, missions, agents,
# evidence -> findings -> recommendations -> experiments, baselines, usage.
# ============================================================================


class MerchantProfile(Base, TimestampMixin):
    """Strategy-team view of a merchant: identity + commercial context (PRD_3 §5).

    Structured scalars for queryable fields, JSON payloads where normalization
    is unnecessary for MVP. One active profile per merchant.
    """

    __tablename__ = "merchant_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    goal_text: Mapped[str | None] = mapped_column(Text)  # "What are you trying to improve?"
    business_description: Mapped[str | None] = mapped_column(Text)
    primary_category: Mapped[str | None] = mapped_column(String(120))
    subcategory: Mapped[str | None] = mapped_column(String(120))
    geography: Mapped[str | None] = mapped_column(String(255))
    value_proposition: Mapped[str | None] = mapped_column(Text)
    positioning: Mapped[str | None] = mapped_column(Text)
    pricing_model: Mapped[str | None] = mapped_column(String(120))
    competitors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)
    segments_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)
    products_summary_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)
    reputation_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    extra_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class MerchantSource(Base, TimestampMixin):
    """A public source observed for a merchant during research (PRD_3 §15)."""

    __tablename__ = "merchant_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # website|review|social|press|video|search|other
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (Index("ix_merchant_sources_merchant_kind", "merchant_id", "kind"),)


class StrategyAgent(Base, TimestampMixin):
    """Registry row describing one specialist agent and its contract (PRD_3 §7/§8)."""

    __tablename__ = "strategy_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # market | competitor | buyer | presence | strategy
    key: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    allowed_tools_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    mission_types_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    output_schema_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Mission(Base, TimestampMixin):
    """Bounded objective executed by one or more agents (PRD_3 §9); billing unit.

    Lifecycle (PRD_3 §26): CREATED -> QUEUED -> RUNNING ->
    COMPLETED | FAILED | CANCELLED | TIMED_OUT (+ PARTIALLY_COMPLETED on budget stop).
    """

    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    mission_type: Mapped[str] = mapped_column(
        String(48), default="on_demand", nullable=False, index=True
    )  # baseline|recurring|on_demand|experiment
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    budget_runs: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    agent_assignments_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    runs_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (
        Index("ix_missions_merchant_status_created", "merchant_id", "status", "created_at"),
        Index("ix_missions_merchant_type_created", "merchant_id", "mission_type", "created_at"),
    )


class AgentRun(Base, TimestampMixin):
    """One bounded execution of one agent within a mission (PRD_3 §27)."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_key: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    budget_tool_calls: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float | None] = mapped_column()
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_agent_runs_mission_status", "mission_id", "status"),)


class Evidence(Base, TimestampMixin):
    """Provenance-backed observation supporting findings (PRD_3 §16/§28)."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_type: Mapped[str] = mapped_column(String(48), default="website", nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # fact | inference | speculation (PRD_3 §28)
    epistemic_state: Mapped[str] = mapped_column(String(24), default="fact", nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (Index("ix_evidence_mission_created", "mission_id", "created_at"),)


class Finding(Base, TimestampMixin):
    """Interpreted observation supported by evidence (PRD_3 §17)."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_key: Mapped[str | None] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    tags_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)

    __table_args__ = (Index("ix_findings_mission_created", "mission_id", "created_at"),)


class Recommendation(Base, TimestampMixin):
    """Actionable strategic conclusion derived from findings (PRD_3 §18/§20)."""

    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str | None] = mapped_column(String(36), index=True)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    recommendation_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_impact: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    is_hypothesis: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finding_ids_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSONVariant, default=list)
    suggested_next_mission_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    priority_rank: Mapped[int | None] = mapped_column(Integer)
    impact: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="proposed", nullable=False, index=True
    )  # proposed|accepted|rejected|superseded


class Experiment(Base, TimestampMixin):
    """Control-vs-variant simulated counterfactual test (PRD_3 §19).

    MVP experiments are always SIMULATED; results must never be presented as
    real production revenue.
    """

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_id: Mapped[str | None] = mapped_column(String(36), index=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(String(64), default="buyer_selection_rate", nullable=False)
    control_variant_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    treatment_variant_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    cohort_size: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    cohort_definition_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False, index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentRun(Base, TimestampMixin):
    """One buyer-simulation run inside one experiment arm (PRD_3 §19)."""

    __tablename__ = "experiment_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    arm: Mapped[str] = mapped_column(String(16), nullable=False)  # control|treatment
    buyer_prompt: Mapped[str | None] = mapped_column(Text)
    selected: Mapped[bool | None] = mapped_column(Boolean)
    selection_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    error_text: Mapped[str | None] = mapped_column(Text)


class BaselineSnapshot(Base, TimestampMixin):
    """Versioned Day-0 diagnostic snapshot (PRD_3 §6.2)."""

    __tablename__ = "baseline_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mission_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="complete", nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (UniqueConstraint("merchant_id", "version", name="uq_baseline_version"),)


class UsageEvent(Base):
    """Granular usage accounting for future billing (PRD_3 §10/§23.11)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    merchant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mission_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(
        String(48), nullable=False
    )  # mission_created|agent_run|tool_call|llm_call
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost_estimate_minor: Mapped[int | None] = mapped_column(BigInteger)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (Index("ix_usage_events_merchant_kind_created", "merchant_id", "kind", "created_at"),)


class MemoryRef(Base, TimestampMixin):
    """Bookkeeping linking durable semantic memories to their provider records.

    PostgreSQL keeps structured truth; Mem0/local adapter holds semantic
    memory. This table lets us audit what was stored where (PRD_3 §14).
    """

    __tablename__ = "memory_refs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # mem0|local
    provider_memory_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)  # goal|fact|observation|outcome|preference
    text_preview: Mapped[str | None] = mapped_column(Text)
    mission_id: Mapped[str | None] = mapped_column(String(36), index=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (UniqueConstraint("provider", "provider_memory_id", name="uq_memory_provider_id"),)
