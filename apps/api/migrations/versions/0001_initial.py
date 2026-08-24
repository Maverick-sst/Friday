"""initial schema per PRD §19

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _created_updated() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "merchants",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("subcategory", sa.String(120), nullable=True),
        sa.Column("website_url", sa.String(512), nullable=True),
        sa.Column("logo_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("agent_endpoint", sa.String(512), nullable=True),
        *_created_updated(),
    )

    op.create_table(
        "merchant_integrations",
        _uuid_pk(),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("store_url", sa.String(512), nullable=True),
        sa.Column("auth_reference_encrypted", sa.Text(), nullable=True),
        sa.Column("scopes_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        *_created_updated(),
        sa.UniqueConstraint("merchant_id", "provider", name="uq_merchant_provider"),
    )

    op.create_table(
        "merchant_capabilities",
        _uuid_pk(),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("capability_name", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_json", JSONB(), nullable=True),
        *_created_updated(),
        sa.UniqueConstraint("merchant_id", "capability_name", name="uq_merchant_capability"),
    )

    op.create_table(
        "merchant_policies",
        _uuid_pk(),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("max_auto_purchase", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("approval_threshold", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("allowed_categories_json", JSONB(), nullable=True),
        sa.Column("allowed_regions_json", JSONB(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("return_window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("allow_cancellation", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_created_updated(),
    )

    op.create_table(
        "products",
        _uuid_pk(),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("external_id", sa.String(120), nullable=True, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True, index=True),
        sa.Column("brand", sa.String(120), nullable=True, index=True),
        sa.Column("product_url", sa.String(512), nullable=True),
        sa.Column("image_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("source", sa.String(32), nullable=False, server_default="shopify"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        *_created_updated(),
    )

    op.create_table(
        "product_variants",
        _uuid_pk(),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("external_id", sa.String(120), nullable=True, index=True),
        sa.Column("sku", sa.String(120), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("available_for_sale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("options_json", JSONB(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        *_created_updated(),
    )

    op.create_table(
        "agent_sessions",
        _uuid_pk(),
        sa.Column("session_id", sa.String(64), nullable=False, unique=True),
        sa.Column("buyer_id", sa.String(120), nullable=True),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("user_intent", sa.Text(), nullable=True),
        sa.Column("constraints_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "quotes",
        _uuid_pk(),
        sa.Column("quote_ref", sa.String(64), nullable=False, unique=True),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id"), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=True, index=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_id", sa.String(36), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("subtotal", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("shipping_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("inventory_snapshot", JSONB(), nullable=True),
        sa.Column("live_validated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(32), nullable=False, server_default="shopify"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "carts",
        _uuid_pk(),
        sa.Column("cart_ref", sa.String(64), nullable=False, unique=True),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id"), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=True, index=True),
        sa.Column("external_cart_id", sa.String(120), nullable=True),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id"), nullable=True),
        sa.Column("items_json", JSONB(), nullable=True),
        sa.Column("total_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        *_created_updated(),
    )

    op.create_table(
        "transactions",
        _uuid_pk(),
        sa.Column("txn_ref", sa.String(64), nullable=False, unique=True),
        sa.Column("session_id", sa.String(64), nullable=True, index=True),
        sa.Column("merchant_id", sa.String(36), sa.ForeignKey("merchants.id"), nullable=False, index=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("variant_id", sa.String(36), sa.ForeignKey("product_variants.id"), nullable=True),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id"), nullable=True),
        sa.Column("cart_id", sa.String(36), sa.ForeignKey("carts.id"), nullable=True),
        sa.Column("requested_amount", sa.BigInteger(), nullable=True),
        sa.Column("quoted_amount", sa.BigInteger(), nullable=True),
        sa.Column("authorized_amount", sa.BigInteger(), nullable=True),
        sa.Column("final_amount", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED", index=True),
        sa.Column("shopify_reference", sa.String(255), nullable=True),
        sa.Column("razorpay_order_id", sa.String(120), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(120), nullable=True),
        *_created_updated(),
        sa.Index("ix_transactions_session_status", "session_id", "status"),
    )

    op.create_table(
        "transaction_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transaction_id", sa.String(36), sa.ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=True),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("response_snapshot", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("transaction_events")
    op.drop_table("transactions")
    op.drop_table("carts")
    op.drop_table("quotes")
    op.drop_table("agent_sessions")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("merchant_policies")
    op.drop_table("merchant_capabilities")
    op.drop_table("merchant_integrations")
    op.drop_table("merchants")
