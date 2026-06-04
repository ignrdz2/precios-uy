from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class SupermarketProduct(Base):
    __tablename__ = "supermarket_products"
    __table_args__ = (UniqueConstraint("supermarket_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    supermarket_id: Mapped[int] = mapped_column(ForeignKey("supermarkets.id"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    name_raw: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(500))
    image_url: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product | None"] = relationship(back_populates="supermarket_products")  # noqa: F821
    supermarket: Mapped["Supermarket"] = relationship(back_populates="supermarket_products")  # noqa: F821
    price_history: Mapped[list["PriceHistory"]] = relationship(back_populates="supermarket_product")  # noqa: F821
