from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date, String

from app.db.session import get_db
from app.models.product import Product
from app.schemas.common import PaginatedResponse
from app.schemas.product import (
    CompareEntryResponse,
    CompareResponse,
    CurrentPriceResponse,
    PriceHistoryResponse,
    PricePointResponse,
    ProductDetailResponse,
    ProductSummaryResponse,
    SupermarketProductDetailResponse,
)

router = APIRouter(prefix="/products", tags=["products"])

# ---------------------------------------------------------------------------
# SQL reutilizable
# ---------------------------------------------------------------------------

# Subquery de ventana para obtener el precio más reciente por supermarket_product.
# Usa ROW_NUMBER() OVER (...) en lugar de LATERAL para compatibilidad con SQLite
# (tests) y PostgreSQL (producción).
_LAST_PRICE_INNER = """(
    SELECT supermarket_product_id, price, currency, date,
           ROW_NUMBER() OVER (
               PARTITION BY supermarket_product_id
               ORDER BY date DESC, scraped_at DESC
           ) AS _rn
    FROM price_history
) ph"""

_JOIN_LAST_PRICE = f"JOIN {_LAST_PRICE_INNER} ON ph.supermarket_product_id = sp.id AND ph._rn = 1"
_LEFT_JOIN_LAST_PRICE = f"LEFT JOIN {_LAST_PRICE_INNER} ON ph.supermarket_product_id = sp.id AND ph._rn = 1"

# lower() LIKE lower() en lugar de ILIKE para compatibilidad con SQLite
_WHERE_FILTERS = """
    (:q IS NULL OR lower(p.name) LIKE lower(:q_pattern))
    AND (:category IS NULL OR p.category = :category)
    AND (:supermarket IS NULL OR sm.slug = :supermarket)
"""

_FILTER_BINDPARAMS = [
    bindparam("q", type_=String),
    bindparam("q_pattern", type_=String),
    bindparam("category", type_=String),
    bindparam("supermarket", type_=String),
]

_COUNT_SQL = text(f"""
    SELECT COUNT(DISTINCT p.id)
    FROM products p
    JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
    JOIN supermarkets sm ON sm.id = sp.supermarket_id AND sm.active = true
    {_JOIN_LAST_PRICE}
    WHERE {_WHERE_FILTERS}
""").bindparams(*_FILTER_BINDPARAMS)

_LIST_SQL = text(f"""
    WITH paginated_ids AS (
        SELECT DISTINCT p.id
        FROM products p
        JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
        JOIN supermarkets sm ON sm.id = sp.supermarket_id AND sm.active = true
        {_JOIN_LAST_PRICE}
        WHERE {_WHERE_FILTERS}
        ORDER BY p.id
        LIMIT :page_size OFFSET :offset
    )
    SELECT
        p.id,
        p.name,
        p.category,
        p.brand,
        p.unit,
        sm.slug  AS supermarket_slug,
        sm.name  AS supermarket_name,
        sp.url,
        sp.image_url,
        ph.price,
        ph.currency,
        ph.date  AS last_updated
    FROM paginated_ids pi
    JOIN products p             ON p.id = pi.id
    JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
    JOIN supermarkets sm        ON sm.id = sp.supermarket_id AND sm.active = true
    {_JOIN_LAST_PRICE}
    ORDER BY p.id, sm.slug
""").bindparams(*_FILTER_BINDPARAMS)

_DETAIL_SPS_SQL = text(f"""
    SELECT
        sp.id,
        sp.name_raw,
        sp.url,
        sp.image_url,
        sm.slug  AS supermarket_slug,
        sm.name  AS supermarket_name,
        ph.price    AS current_price,
        ph.currency,
        ph.date     AS last_updated
    FROM supermarket_products sp
    JOIN supermarkets sm ON sm.id = sp.supermarket_id
    {_LEFT_JOIN_LAST_PRICE}
    WHERE sp.product_id = :product_id
      AND sp.active = true
    ORDER BY sm.slug
""")

# Precios actuales por supermercado, solo los que tienen historial, ordenados precio ASC
_PRICES_SQL = text(f"""
    SELECT
        sm.slug  AS supermarket_slug,
        sm.name  AS supermarket_name,
        ph.price,
        ph.currency,
        ph.date  AS last_updated,
        sp.url,
        sp.image_url
    FROM supermarket_products sp
    JOIN supermarkets sm ON sm.id = sp.supermarket_id
    {_JOIN_LAST_PRICE}
    WHERE sp.product_id = :product_id
      AND sp.active = true
    ORDER BY ph.price ASC
""")

# Serie histórica completa, filtrable por rango de fechas, ordenada para Recharts
_HISTORY_SQL = text("""
    SELECT
        s.slug,
        ph.date,
        ph.price
    FROM price_history ph
    JOIN supermarket_products sp ON ph.supermarket_product_id = sp.id
    JOIN supermarkets s           ON sp.supermarket_id = s.id
    WHERE sp.product_id = :product_id
      AND (:from_date IS NULL OR ph.date >= :from_date)
      AND (:to_date   IS NULL OR ph.date <= :to_date)
    ORDER BY s.slug, ph.date ASC
""").bindparams(
    bindparam("from_date", type_=Date),
    bindparam("to_date", type_=Date),
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[ProductSummaryResponse])
async def list_products(
    q: str | None = None,
    category: str | None = None,
    supermarket: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ProductSummaryResponse]:
    q_pattern = f"%{q}%" if q else None
    params = {"q": q, "q_pattern": q_pattern, "category": category, "supermarket": supermarket}

    total: int = (await db.execute(_COUNT_SQL, params)).scalar_one()

    if total == 0:
        return PaginatedResponse[ProductSummaryResponse](
            items=[], total=0, page=page, page_size=page_size
        )

    offset = (page - 1) * page_size
    rows = (
        await db.execute(_LIST_SQL, {**params, "page_size": page_size, "offset": offset})
    ).mappings().all()

    # Agrupar filas por product.id — cada fila es un (producto, supermercado) par
    products_map: dict[int, dict] = {}
    for row in rows:
        pid = row["id"]
        if pid not in products_map:
            products_map[pid] = {
                "id": pid,
                "name": row["name"],
                "category": row["category"],
                "brand": row["brand"],
                "unit": row["unit"],
                "current_prices": [],
            }
        products_map[pid]["current_prices"].append(
            CurrentPriceResponse(
                supermarket_slug=row["supermarket_slug"],
                supermarket_name=row["supermarket_name"],
                price=row["price"],
                currency=row["currency"],
                last_updated=row["last_updated"],
                url=row["url"],
                image_url=row["image_url"],
            )
        )

    items: list[ProductSummaryResponse] = []
    for data in products_map.values():
        prices = [cp.price for cp in data["current_prices"]]
        items.append(
            ProductSummaryResponse(
                id=data["id"],
                name=data["name"],
                category=data["category"],
                brand=data["brand"],
                unit=data["unit"],
                current_prices=data["current_prices"],
                min_price=min(prices) if prices else None,
            )
        )

    return PaginatedResponse[ProductSummaryResponse](
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> ProductDetailResponse:
    product = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()

    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    rows = (
        await db.execute(_DETAIL_SPS_SQL, {"product_id": product_id})
    ).mappings().all()

    supermarket_products = [
        SupermarketProductDetailResponse(
            id=row["id"],
            supermarket_slug=row["supermarket_slug"],
            supermarket_name=row["supermarket_name"],
            name_raw=row["name_raw"],
            url=row["url"],
            image_url=row["image_url"],
            current_price=row["current_price"],
            currency=row["currency"],
            last_updated=row["last_updated"],
        )
        for row in rows
    ]

    return ProductDetailResponse(
        id=product.id,
        name=product.name,
        category=product.category,
        brand=product.brand,
        unit=product.unit,
        created_at=product.created_at,
        updated_at=product.updated_at,
        supermarket_products=supermarket_products,
    )


async def _get_product_or_404(product_id: int, db: AsyncSession) -> Product:
    product = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return product


async def _fetch_current_prices(product_id: int, db: AsyncSession) -> list[CurrentPriceResponse]:
    rows = (
        await db.execute(_PRICES_SQL, {"product_id": product_id})
    ).mappings().all()
    return [
        CurrentPriceResponse(
            supermarket_slug=row["supermarket_slug"],
            supermarket_name=row["supermarket_name"],
            price=row["price"],
            currency=row["currency"],
            last_updated=row["last_updated"],
            url=row["url"],
            image_url=row["image_url"],
        )
        for row in rows
    ]


@router.get("/{product_id}/prices", response_model=list[CurrentPriceResponse])
async def get_product_prices(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[CurrentPriceResponse]:
    await _get_product_or_404(product_id, db)
    return await _fetch_current_prices(product_id, db)


@router.get("/{product_id}/history", response_model=PriceHistoryResponse)
async def get_product_history(
    product_id: int,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: AsyncSession = Depends(get_db),
) -> PriceHistoryResponse:
    product = await _get_product_or_404(product_id, db)

    rows = (
        await db.execute(
            _HISTORY_SQL,
            {"product_id": product_id, "from_date": from_date, "to_date": to_date},
        )
    ).mappings().all()

    series: dict[str, list[PricePointResponse]] = {}
    for row in rows:
        slug = row["slug"]
        if slug not in series:
            series[slug] = []
        series[slug].append(PricePointResponse(date=row["date"], price=row["price"]))

    return PriceHistoryResponse(
        product_id=product.id,
        product_name=product.name,
        series=series,
    )


@router.get("/{product_id}/compare", response_model=CompareResponse)
async def compare_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> CompareResponse:
    product = await _get_product_or_404(product_id, db)
    current_prices = await _fetch_current_prices(product_id, db)

    # current_prices ya viene ordenado por price ASC desde _PRICES_SQL
    comparison = [
        CompareEntryResponse(
            supermarket_slug=cp.supermarket_slug,
            supermarket_name=cp.supermarket_name,
            price=cp.price,
            currency=cp.currency,
            last_updated=cp.last_updated,
            url=cp.url,
        )
        for cp in current_prices
    ]

    cheapest: str | None = None
    difference: Decimal | None = None
    difference_pct: float | None = None

    if len(current_prices) >= 1:
        cheapest = current_prices[0].supermarket_slug

    if len(current_prices) >= 2:
        min_price = current_prices[0].price
        max_price = current_prices[-1].price
        difference = round(max_price - min_price, 2)
        difference_pct = round(float(difference / min_price) * 100, 2)
    elif len(current_prices) == 1:
        difference = Decimal("0")
        difference_pct = 0.0

    product_summary = ProductSummaryResponse(
        id=product.id,
        name=product.name,
        category=product.category,
        brand=product.brand,
        unit=product.unit,
        current_prices=current_prices,
        min_price=current_prices[0].price if current_prices else None,
    )

    return CompareResponse(
        product=product_summary,
        comparison=comparison,
        cheapest=cheapest,
        difference=difference,
        difference_pct=difference_pct,
    )
