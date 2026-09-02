"""Demo overrides: deterministic mutations for failure demos (PRD §25/§26).

Both the MockAdapter and ShopifyAdapter consult this table before returning
live state, so the dashboard can flip a switch and force a price change or an
inventory race without touching real store data.
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import new_uuid
from app.db.models import Base, TimestampMixin


class DemoOverride(Base, TimestampMixin):
    __tablename__ = "demo_overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merchant_id: Mapped[str] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_external_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available_for_sale: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def apply(self, price_minor: int, available: bool, quantity: int | None) -> tuple[int, bool, int | None]:
        if self.price_minor is not None:
            price_minor = self.price_minor
        if self.available_for_sale is not None:
            available = self.available_for_sale
        if self.available_quantity is not None:
            quantity = self.available_quantity
        return price_minor, available, quantity
