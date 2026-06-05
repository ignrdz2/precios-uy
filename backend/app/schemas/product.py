from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CurrentPriceResponse(BaseModel):
    supermarket_slug: str
    supermarket_name: str
    price: Decimal
    currency: str
    last_updated: date
    url: str | None
    image_url: str | None


class ProductSummaryResponse(BaseModel):
    id: int
    name: str
    category: str | None
    brand: str | None
    unit: str | None
    current_prices: list[CurrentPriceResponse]
    min_price: Decimal | None


class SupermarketProductDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supermarket_slug: str
    supermarket_name: str
    name_raw: str
    url: str | None
    image_url: str | None
    current_price: Decimal | None
    currency: str | None
    last_updated: date | None


class ProductDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str | None
    brand: str | None
    unit: str | None
    created_at: datetime
    updated_at: datetime
    supermarket_products: list[SupermarketProductDetailResponse]


class PricePointResponse(BaseModel):
    date: date
    price: Decimal


class PriceHistoryResponse(BaseModel):
    product_id: int
    product_name: str
    series: dict[str, list[PricePointResponse]]


class CompareEntryResponse(BaseModel):
    supermarket_slug: str
    supermarket_name: str
    price: Decimal
    currency: str
    last_updated: date
    url: str | None


class CompareResponse(BaseModel):
    product: ProductSummaryResponse
    comparison: list[CompareEntryResponse]
    cheapest: str | None
    difference: Decimal | None
    difference_pct: float | None
