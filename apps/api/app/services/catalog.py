"""Catalog read paths (PRD §16 discovery path): served from our canonical DB."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.models import Product, ProductVariant
from app.domain.contracts import Product as ProductContract
from app.domain.contracts import ProductVariant as VariantContract
from app.domain.contracts import SearchProductsRequest


def _to_contract(product: Product, variants: list[ProductVariant]) -> ProductContract:
    return ProductContract(
        id=product.id,
        external_id=product.external_id,
        title=product.title,
        description=product.description,
        category=product.category,
        brand=product.brand,
        product_url=product.product_url,
        image_url=product.image_url,
        status=product.status,
        variants=[
            VariantContract(
                id=v.id,
                external_id=v.external_id,
                sku=v.sku,
                title=v.title,
                options=dict(v.options_json or {}),
                price_minor=v.price,
                currency=v.currency,
                available_quantity=v.available_quantity,
                available_for_sale=v.available_for_sale,
            )
            for v in variants
        ],
    )


def search_products(
    db: Session, merchant_id: str, req: SearchProductsRequest
) -> list[ProductContract]:
    stmt = select(Product).where(Product.merchant_id == merchant_id, Product.status == "active")
    q = (req.query or "").strip().lower()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Product.title.ilike(like)
            | Product.brand.ilike(like)
            | Product.category.ilike(like)
            | Product.description.ilike(like)
        )
    if req.filters.category:
        stmt = stmt.where(Product.category == req.filters.category)
    if req.filters.brand:
        stmt = stmt.where(Product.brand == req.filters.brand)

    products = list(db.scalars(stmt.order_by(Product.title.asc()).limit(req.limit * 2)))
    results: list[ProductContract] = []
    for product in products:
        variant_rows = list(
            db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id))
        )
        if req.filters.available_only:
            variant_rows = [v for v in variant_rows if v.available_for_sale]
            if not variant_rows:
                continue
        if req.filters.max_price_minor is not None:
            variant_rows = [v for v in variant_rows if v.price <= req.filters.max_price_minor]
            if not variant_rows:
                continue
        results.append(_to_contract(product, variant_rows))
        if len(results) >= req.limit:
            break
    return results


def get_product_or_404(
    db: Session, merchant_id: str, product_ref: str
) -> tuple[Product, list[ProductVariant]]:
    stmt = select(Product).where(Product.merchant_id == merchant_id)
    product = db.scalar(stmt.where(Product.id == product_ref)) or db.scalar(
        stmt.where(Product.external_id == product_ref)
    )
    if product is None:
        raise not_found("PRODUCT_NOT_FOUND", f"No product {product_ref} for this merchant")
    variants = list(db.scalars(select(ProductVariant).where(ProductVariant.product_id == product.id)))
    return product, variants


def get_variant(db: Session, product: Product, variant_ref: str) -> ProductVariant | None:
    return db.scalar(
        select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            (ProductVariant.id == variant_ref) | (ProductVariant.external_id == variant_ref),
        )
    )
