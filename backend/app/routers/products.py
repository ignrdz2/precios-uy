from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.product import Product
from app.schemas.common import PaginatedResponse
from app.schemas.product import (
    CurrentPriceResponse,
    ProductDetailResponse,
    ProductSummaryResponse,
    SupermarketProductDetailResponse,
)

router = APIRouter(prefix="/products", tags=["products"])

# ---------------------------------------------------------------------------
# SQL reutilizable
# ---------------------------------------------------------------------------

# Filtros comunes a COUNT y al CTE de paginación
_WHERE_FILTERS = """
    (:q IS NULL OR p.name ILIKE :q_pattern)
    AND (:category IS NULL OR p.category = :category)
    AND (:supermarket IS NULL OR sm.slug = :supermarket)
"""

# Obtiene el último precio de un supermarket_product vía LATERAL
_LATERAL_LAST_PRICE = """
    JOIN LATERAL (
        SELECT price, currency, date
        FROM price_history
        WHERE supermarket_product_id = sp.id
        ORDER BY date DESC, scraped_at DESC
        LIMIT 1
    ) ph ON true
"""

_COUNT_SQL = text(f"""
    SELECT COUNT(DISTINCT p.id)
    FROM products p
    JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
    JOIN supermarkets sm ON sm.id = sp.supermarket_id AND sm.active = true
    {_LATERAL_LAST_PRICE}
    WHERE {_WHERE_FILTERS}
""")

_LIST_SQL = text(f"""
    WITH paginated_ids AS (
        SELECT DISTINCT p.id
        FROM products p
        JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
        JOIN supermarkets sm ON sm.id = sp.supermarket_id AND sm.active = true
        {_LATERAL_LAST_PRICE}
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
    {_LATERAL_LAST_PRICE}
    ORDER BY p.id, sm.slug
""")

_DETAIL_SPS_SQL = text("""
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
    LEFT JOIN LATERAL (
        SELECT price, currency, date
        FROM price_history
        WHERE supermarket_product_id = sp.id
        ORDER BY date DESC, scraped_at DESC
        LIMIT 1
    ) ph ON true
    WHERE sp.product_id = :product_id
      AND sp.active = true
    ORDER BY sm.slug
""")


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
