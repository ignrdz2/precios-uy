from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.supermarket import Supermarket
from app.schemas.product import CurrentPriceResponse, ProductSummaryResponse
from app.schemas.supermarket import SupermarketResponse

router = APIRouter(prefix="/supermarkets", tags=["supermarkets"])


class SupermarketProductsResponse(BaseModel):
    supermarket: SupermarketResponse
    items: list[ProductSummaryResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# SQL reutilizable
# ---------------------------------------------------------------------------

# Subquery de ventana reutilizable (igual que en products.py, portable con SQLite)
_SM_LAST_PRICE_INNER = """(
    SELECT supermarket_product_id, price, currency, date,
           ROW_NUMBER() OVER (
               PARTITION BY supermarket_product_id
               ORDER BY date DESC, scraped_at DESC
           ) AS _rn
    FROM price_history
) ph"""

_SM_JOIN_LAST_PRICE = (
    f"JOIN {_SM_LAST_PRICE_INNER} ON ph.supermarket_product_id = sp.id AND ph._rn = 1"
)

# EXISTS en lugar de LATERAL para el chequeo de "tiene al menos un precio"
_SM_COUNT_SQL = text("""
    SELECT COUNT(DISTINCT p.id)
    FROM products p
    JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
    JOIN supermarkets sm         ON sm.id = sp.supermarket_id
                                AND sm.slug = :slug
                                AND sm.active = true
    WHERE EXISTS (SELECT 1 FROM price_history WHERE supermarket_product_id = sp.id)
""")

# Productos paginados de un supermercado, con el precio de ESE supermercado únicamente
_SM_PRODUCTS_SQL = text(f"""
    WITH paginated_ids AS (
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN supermarket_products sp ON sp.product_id = p.id AND sp.active = true
        JOIN supermarkets sm         ON sm.id = sp.supermarket_id
                                    AND sm.slug = :slug
                                    AND sm.active = true
        WHERE EXISTS (SELECT 1 FROM price_history WHERE supermarket_product_id = sp.id)
        ORDER BY p.name ASC
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
    JOIN supermarkets sm        ON sm.id = sp.supermarket_id
                               AND sm.slug = :slug
                               AND sm.active = true
    {_SM_JOIN_LAST_PRICE}
    ORDER BY p.name ASC
""")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SupermarketResponse])
async def list_supermarkets(
    db: AsyncSession = Depends(get_db),
) -> list[SupermarketResponse]:
    result = await db.execute(
        select(Supermarket)
        .where(Supermarket.active == True)  # noqa: E712
        .order_by(Supermarket.name)
    )
    return [SupermarketResponse.model_validate(sm) for sm in result.scalars().all()]


@router.get("/{slug}/products", response_model=SupermarketProductsResponse)
async def get_supermarket_products(
    slug: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SupermarketProductsResponse:
    supermarket = (
        await db.execute(
            select(Supermarket)
            .where(Supermarket.slug == slug, Supermarket.active == True)  # noqa: E712
        )
    ).scalar_one_or_none()

    if supermarket is None:
        raise HTTPException(status_code=404, detail="Supermercado no encontrado")

    total: int = (
        await db.execute(_SM_COUNT_SQL, {"slug": slug})
    ).scalar_one()

    items: list[ProductSummaryResponse] = []

    if total > 0:
        offset = (page - 1) * page_size
        rows = (
            await db.execute(_SM_PRODUCTS_SQL, {"slug": slug, "page_size": page_size, "offset": offset})
        ).mappings().all()

        # Cada fila es un producto con precio de este supermercado únicamente
        for row in rows:
            current_price = CurrentPriceResponse(
                supermarket_slug=row["supermarket_slug"],
                supermarket_name=row["supermarket_name"],
                price=row["price"],
                currency=row["currency"],
                last_updated=row["last_updated"],
                url=row["url"],
                image_url=row["image_url"],
            )
            items.append(
                ProductSummaryResponse(
                    id=row["id"],
                    name=row["name"],
                    category=row["category"],
                    brand=row["brand"],
                    unit=row["unit"],
                    current_prices=[current_price],
                    min_price=row["price"],
                )
            )

    return SupermarketProductsResponse(
        supermarket=SupermarketResponse.model_validate(supermarket),
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if page_size else 0,
    )
