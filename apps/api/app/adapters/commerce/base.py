"""Commerce adapter protocol (PRD §17.5).

Adapters translate between a merchant's source platform and the canonical
commerce model. V0 ships MockAdapter + ShopifyAdapter only.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass(slots=True)
class LiveVariantState:
    """Authoritative source-of-truth state for a variant at validation time."""

    external_variant_id: str
    external_product_id: str | None
    price_minor: int
    currency: str
    available_for_sale: bool
    available_quantity: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MerchantMetadata:
    name: str
    slug: str
    description: str | None
    category: str | None
    website_url: str | None
    logo_url: str | None
    currency: str


@dataclass(slots=True)
class SyncResult:
    products_synced: int = 0
    variants_synced: int = 0
    skipped: int = 0


@dataclass(slots=True)
class SourceOrderResult:
    reference: str
    raw: dict[str, Any] = field(default_factory=dict)


class CommerceAdapter(Protocol):
    """Every platform adapter implements this surface."""

    provider: str

    def fetch_merchant_metadata(self, store_ref: str) -> MerchantMetadata:
        """Fetch identity metadata for a store reference (URL/domain)."""
        ...

    def sync_catalog(self, db: Session, merchant_id: str) -> SyncResult:
        """Pull catalog from the source into canonical tables."""
        ...

    def live_validate_variant(
        self, db: Session, merchant_id: str, variant_external_id: str
    ) -> LiveVariantState:
        """Re-validate current price/availability at transaction time.

        This is the stale-data protection boundary (PRD §16): discovery may use
        our DB copy, but payment decisions must be validated against live state.
        """
        ...

    def create_source_order(self, db: Session, merchant_id: str, order: dict[str, Any]) -> SourceOrderResult:
        """Optionally push a completed purchase back to the source platform."""
        ...
