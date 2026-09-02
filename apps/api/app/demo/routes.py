"""Demo control endpoints: deterministic failure injection (PRD §25/§26).

These power the dashboard's "Demo Controls" panel - flip a switch, the next
live validation sees a different price/inventory, and the policy engine must
block the payment.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.demo_overrides import DemoOverride
from app.db.models import Merchant, Product, ProductVariant
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/demo", tags=["demo-controls"])


class OverrideRequest(BaseModel):
    merchant_id: str
    target_external_id: str
    price_minor: int | None = Field(default=None, ge=0)
    available_for_sale: bool | None = None
    available_quantity: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=255)


@router.get("/variants")
def list_variants(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    rows = list(
        db.scalars(
            select(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.merchant_id == merchant.id)
            .order_by(Product.title, ProductVariant.id)
        )
    )
    return {
        "variants": [
            {
                "external_id": v.external_id,
                "product_title": v.product.title if v.product else None,
                "title": v.title,
                "options": v.options_json,
                "price_minor": v.price,
                "available_for_sale": v.available_for_sale,
                "available_quantity": v.available_quantity,
            }
            for v in rows
        ]
    }


@router.get("/overrides")
def list_overrides(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    rows = list(
        db.scalars(
            select(DemoOverride).where(DemoOverride.merchant_id == merchant.id, DemoOverride.active.is_(True))
        )
    )
    return {
        "overrides": [
            {
                "id": o.id,
                "target_external_id": o.target_external_id,
                "price_minor": o.price_minor,
                "available_for_sale": o.available_for_sale,
                "available_quantity": o.available_quantity,
                "note": o.note,
            }
            for o in rows
        ]
    }


@router.post("/overrides")
def set_override(req: OverrideRequest, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, req.merchant_id)

    existing = db.scalar(
        select(DemoOverride).where(
            DemoOverride.merchant_id == merchant.id,
            DemoOverride.target_external_id == req.target_external_id,
            DemoOverride.active.is_(True),
        )
    )
    if existing is None:
        existing = DemoOverride(merchant_id=merchant.id, target_external_id=req.target_external_id)
        db.add(existing)
    existing.price_minor = req.price_minor
    existing.available_for_sale = req.available_for_sale
    existing.available_quantity = req.available_quantity
    existing.note = req.note
    existing.active = True
    db.commit()
    return {"ok": True, "override_id": existing.id}


@router.post("/scenarios/price-change")
def scenario_price_change(db: Session = Depends(get_db)):
    """The mandatory failure demo: quoted ₹4,799 becomes live ₹5,799."""
    merchant = _merchant_or_404(db, "velocity-sports")
    target = db.scalar(
        select(DemoOverride).where(
            DemoOverride.merchant_id == merchant.id,
            DemoOverride.target_external_id == "mock-var-ds-9-black",
        )
    )
    if target is None:
        target = DemoOverride(merchant_id=merchant.id, target_external_id="mock-var-ds-9-black")
        db.add(target)
    target.price_minor = 579900
    target.available_for_sale = True
    target.note = "Failure demo: price changed after quote"
    target.active = True
    db.commit()
    return {
        "scenario": "PRICE_CHANGE_AFTER_QUOTE",
        "variant": "mock-var-ds-9-black",
        "new_price_minor": 579900,
        "expected_result": "BLOCKED: FINAL_AMOUNT_EXCEEDS_BUYER_AUTHORIZATION / PRICE_CHANGED_SINCE_QUOTE",
    }


@router.delete("/overrides/{override_id}")
def clear_override(override_id: str, db: Session = Depends(get_db)):
    row = db.get(DemoOverride, override_id)
    if row is None:
        raise not_found("OVERRIDE_NOT_FOUND", f"No override {override_id}")
    row.active = False
    db.commit()
    return {"ok": True}


@router.post("/reset")
def reset_overrides(merchant_id: str, db: Session = Depends(get_db)):
    merchant = _merchant_or_404(db, merchant_id)
    rows = list(
        db.scalars(
            select(DemoOverride).where(DemoOverride.merchant_id == merchant.id, DemoOverride.active.is_(True))
        )
    )
    for row in rows:
        row.active = False
    db.commit()
    return {"ok": True, "cleared": len(rows)}


def _merchant_or_404(db: Session, slug: str) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.slug == slug))
    if merchant is None:
        raise not_found("MERCHANT_NOT_FOUND", f"No merchant {slug}")
    return merchant
