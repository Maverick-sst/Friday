"""demo overrides table for deterministic failure demos

Revision ID: 0002_demo_overrides
Revises: 0001_initial
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_demo_overrides"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "demo_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "merchant_id",
            sa.String(36),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_external_id", sa.String(120), nullable=False, index=True),
        sa.Column("price_minor", sa.BigInteger(), nullable=True),
        sa.Column("available_for_sale", sa.Boolean(), nullable=True),
        sa.Column("available_quantity", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("demo_overrides")
