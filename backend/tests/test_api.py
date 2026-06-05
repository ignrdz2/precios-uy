"""Tests de la API REST con DB SQLite en memoria.

Ejecutar desde backend/:
    pytest tests/test_api.py -v

Las fixtures db_engine, db_session y client están definidas en conftest.py.
Todos los tests insertan sus propios datos directamente con AsyncSession para
verificar los queries reales (sin mocks de ORM).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_history import PriceHistory
from app.models.product import Product
from app.models.scrape_run import ScrapeRun
from app.models.supermarket import Supermarket
from app.models.supermarket_product import SupermarketProduct


# ---------------------------------------------------------------------------
# Helpers de fixtures de datos
# ---------------------------------------------------------------------------


async def _insert_supermarket(session: AsyncSession, slug: str, name: str, active: bool = True) -> Supermarket:
    sm = Supermarket(slug=slug, name=name, base_url=f"https://{slug}.uy", active=active)
    session.add(sm)
    await session.flush()
    return sm


async def _insert_product(session: AsyncSession, name: str, category: str | None = None) -> Product:
    p = Product(name=name, category=category)
    session.add(p)
    await session.flush()
    return p


async def _insert_supermarket_product(
    session: AsyncSession,
    product: Product,
    supermarket: Supermarket,
) -> SupermarketProduct:
    sp = SupermarketProduct(
        product_id=product.id,
        supermarket_id=supermarket.id,
        name_raw=product.name,
        active=True,
    )
    session.add(sp)
    await session.flush()
    return sp


async def _insert_price(
    session: AsyncSession,
    sp: SupermarketProduct,
    price: Decimal,
    price_date: date | None = None,
) -> PriceHistory:
    ph = PriceHistory(
        supermarket_product_id=sp.id,
        price=price,
        currency="UYU",
        date=price_date or date.today(),
        scraped_at=datetime.now(timezone.utc),
    )
    session.add(ph)
    await session.flush()
    return ph


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_health_sin_scrapes(client: AsyncClient):
    """GET /health devuelve status='ok' y last_scrape=null cuando scrape_runs está vacía."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["last_scrape"] is None


async def test_health_con_scrape(client: AsyncClient, db_session: AsyncSession):
    """GET /health refleja el último ScrapeRun cuando existe."""
    run = ScrapeRun(
        started_at=datetime(2024, 11, 15, 9, 0, tzinfo=timezone.utc),
        finished_at=datetime(2024, 11, 15, 9, 4, tzinfo=timezone.utc),
        status="completed",
        scrape_stats={"tienda_inglesa": {"scraped": 800}, "disco": {"scraped": 650}},
    )
    db_session.add(run)
    await db_session.commit()

    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["last_scrape"]["status"] == "completed"
    assert data["last_scrape"]["total_products_scraped"] == 1450


async def test_products_lista_vacia(client: AsyncClient):
    """GET /api/v1/products devuelve items=[] y total=0 cuando products está vacía."""
    resp = await client.get("/api/v1/products")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


async def test_products_busqueda_por_nombre(client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/products?q=leche filtra solo productos cuyo nombre contiene 'leche'."""
    sm = await _insert_supermarket(db_session, "tienda_inglesa", "Tienda Inglesa")

    p_leche = await _insert_product(db_session, "Leche Conaprole Entera 1L", "Lácteos")
    p_aceite = await _insert_product(db_session, "Aceite Girasol 900ml", "Aceites")

    sp_leche = await _insert_supermarket_product(db_session, p_leche, sm)
    sp_aceite = await _insert_supermarket_product(db_session, p_aceite, sm)

    await _insert_price(db_session, sp_leche, Decimal("90.00"))
    await _insert_price(db_session, sp_aceite, Decimal("120.00"))
    await db_session.commit()

    resp = await client.get("/api/v1/products", params={"q": "leche"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert "leche" in data["items"][0]["name"].lower()


async def test_product_not_found(client: AsyncClient):
    """GET /api/v1/products/99999 devuelve HTTP 404."""
    resp = await client.get("/api/v1/products/99999")
    assert resp.status_code == 404


async def test_compare_un_solo_supermercado(client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/products/{id}/compare con un solo supermercado devuelve difference=0."""
    sm = await _insert_supermarket(db_session, "disco", "Disco")
    p = await _insert_product(db_session, "Leche Pilat 1L")
    sp = await _insert_supermarket_product(db_session, p, sm)
    await _insert_price(db_session, sp, Decimal("88.50"))
    await db_session.commit()

    resp = await client.get(f"/api/v1/products/{p.id}/compare")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cheapest"] == "disco"
    assert Decimal(data["difference"]) == Decimal("0")
    assert data["difference_pct"] == pytest.approx(0.0)


async def test_supermarkets_lista(client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/supermarkets devuelve solo supermercados activos."""
    await _insert_supermarket(db_session, "tienda_inglesa", "Tienda Inglesa", active=True)
    await _insert_supermarket(db_session, "devoto", "Devoto", active=False)
    await db_session.commit()

    resp = await client.get("/api/v1/supermarkets")
    assert resp.status_code == 200
    data = resp.json()
    slugs = [sm["slug"] for sm in data]
    assert "tienda_inglesa" in slugs
    assert "devoto" not in slugs
