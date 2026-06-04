from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CHAR, Date, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    supermarket_product_id: Mapped[int] = mapped_column(ForeignKey("supermarket_products.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="UYU")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    date: Mapped[date] = mapped_column(Date)

    supermarket_product: Mapped["SupermarketProduct"] = relationship(  # noqa: F821
        back_populates="price_history"
    )
