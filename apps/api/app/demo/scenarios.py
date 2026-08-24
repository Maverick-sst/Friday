"""Deterministic failure-demo scenarios (PRD §25/§26).

Applied *between* quote creation and checkout so the policy engine observes a
genuine post-quote mutation - exactly the story the demo must tell.
"""

from sqlalchemy import select

from app.db.demo_overrides import DemoOverride
from app.db.models import AgentSession, Quote

SCENARIO_PRICE_CHANGE = "PRICE_CHANGE_AFTER_QUOTE"
SCENARIO_INVENTORY_RACE = "INVENTORY_RACE"


def _latest_variant_external_id(db, session_row: AgentSession) -> str | None:
    row = db.scalar(
        select(Quote)
        .where(Quote.session_id == session_row.session_id)
        .order_by(Quote.created_at.desc(), Quote.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    from app.db.models import ProductVariant

    variant = db.get(ProductVariant, row.variant_id)
    return (variant.external_id or variant.id) if variant else None


def _activate_override(db, merchant_id: str, external_id: str, **fields) -> None:
    existing = db.scalar(
        select(DemoOverride).where(
            DemoOverride.merchant_id == merchant_id,
            DemoOverride.target_external_id == external_id,
            DemoOverride.active.is_(True),
        )
    )
    if existing is None:
        existing = DemoOverride(merchant_id=merchant_id, target_external_id=external_id)
        db.add(existing)
    for key, value in fields.items():
        setattr(existing, key, value)
    existing.active = True
    db.commit()


def maybe_apply_pre_checkout(db, session_row: AgentSession) -> str | None:
    """Flip the demo switch right before checkout, per session scenario."""
    scenario = (session_row.constraints_json or {}).get("demo_scenario")
    if not scenario:
        return None

    external_id = _latest_variant_external_id(db, session_row)
    if external_id is None:
        return None

    if scenario == SCENARIO_PRICE_CHANGE:
        _activate_override(
            db,
            session_row.merchant_id,
            external_id,
            price_minor=579900,
            note="Failure demo: price changed after quote",
        )
        return SCENARIO_PRICE_CHANGE

    if scenario == SCENARIO_INVENTORY_RACE:
        _activate_override(
            db,
            session_row.merchant_id,
            external_id,
            available_for_sale=False,
            available_quantity=0,
            note="Failure demo: inventory race",
        )
        return SCENARIO_INVENTORY_RACE

    return None
