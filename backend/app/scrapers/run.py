"""Punto de entrada para correr el scrape completo manualmente.

Uso:
    docker compose exec backend python -m app.scrapers.run
    python -m app.scrapers.run  (desde el directorio backend/, con .env cargado)

Criterio de éxito (spec Fase 1):
    Llena la base de datos con productos reales de ambos supermercados,
    correctamente normalizados y con su historial de precios.
"""

import asyncio
import logging
import sys
import time
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.price_history import PriceHistory
from app.models.supermarket import Supermarket
from app.models.supermarket_product import SupermarketProduct
from app.scrapers.base import BaseScraper, ScrapedProduct
from app.scrapers.disco import DiscoScraper
from app.scrapers.tienda_inglesa import TiendaInglesaScraper
from app.services.normalizer import ProductNormalizer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Función principal (llamada por el scheduler y por __main__)
# ---------------------------------------------------------------------------


async def run_all_scrapers() -> None:
    """Orquesta el pipeline completo: scrape → guardar → normalizar."""
    start = time.monotonic()
    logger.info("=== Iniciando scrape completo ===")

    # Verificar conectividad a DB antes de hacer cualquier trabajo costoso
    try:
        async with AsyncSessionLocal() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Base de datos no disponible: %s", exc)
        raise RuntimeError(f"No se puede conectar a la base de datos: {exc}") from exc

    # Correr ambos scrapers en paralelo; return_exceptions=True evita cancelar
    # el otro scraper si uno falla
    scrape_results = await asyncio.gather(
        _run_scraper(TiendaInglesaScraper()),
        _run_scraper(DiscoScraper()),
        return_exceptions=True,
    )

    slugs = ["tienda_inglesa", "disco"]
    scrapers_data: dict[str, list[ScrapedProduct] | BaseException] = dict(
        zip(slugs, scrape_results)
    )

    # Guardar en DB y normalizar dentro de una sola sesión
    async with AsyncSessionLocal() as session:
        supermarkets = await _load_supermarkets(session)
        save_stats: dict[str, dict] = {}

        for slug, result in scrapers_data.items():
            if isinstance(result, BaseException):
                logger.error("[%s] scraper falló con excepción: %s", slug, result)
                save_stats[slug] = {"scraped": 0, "inserted": 0, "updated": 0, "error": True}
                continue

            supermarket = supermarkets.get(slug)
            if not supermarket:
                logger.error(
                    "[%s] supermercado no encontrado en DB — ¿ejecutaste seed.py?", slug
                )
                save_stats[slug] = {"scraped": 0, "inserted": 0, "updated": 0, "error": True}
                continue

            stats = await _save_scraped_products(session, result, supermarket)
            stats["scraped"] = len(result)
            save_stats[slug] = stats
            logger.info(
                "[%s] guardados %d productos (nuevos=%d actualizados=%d)",
                slug,
                len(result),
                stats["inserted"],
                stats["updated"],
            )

        # Commit de todos los upserts antes de normalizar
        await session.commit()

        # Normalizar — solo procesa filas con product_id IS NULL
        norm_stats = await ProductNormalizer().normalize_all(session)

    elapsed = time.monotonic() - start
    _print_summary(save_stats, norm_stats, elapsed)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


async def _run_scraper(scraper: BaseScraper) -> list[ScrapedProduct]:
    """Ejecuta un scraper y devuelve sus productos. Deja propagar las excepciones
    para que asyncio.gather las capture con return_exceptions=True."""
    logger.info("[%s] iniciando scrape...", scraper.supermarket_slug)
    products = await scraper.scrape_all()
    logger.info("[%s] scrape finalizado: %d productos", scraper.supermarket_slug, len(products))
    return products


async def _load_supermarkets(session: AsyncSession) -> dict[str, Supermarket]:
    """Carga todos los supermercados activos de la DB indexados por slug."""
    result = await session.execute(
        select(Supermarket).where(Supermarket.active == True)  # noqa: E712
    )
    return {s.slug: s for s in result.scalars().all()}


async def _save_scraped_products(
    session: AsyncSession,
    scraped: list[ScrapedProduct],
    supermarket: Supermarket,
) -> dict[str, int]:
    """Upsert de productos y registro de precios históricos.

    - Productos CON external_id: batch lookup → actualizar o insertar.
    - Productos SIN external_id: lookup por nombre_raw como fallback (evita duplicados
      en re-ejecuciones, aunque es una garantía más débil).
    - price_history: siempre se inserta un nuevo registro (append-only).
    """
    if not scraped:
        return {"inserted": 0, "updated": 0}

    today = date.today()
    inserted = 0
    updated = 0
    price_records: list[PriceHistory] = []

    # --- Productos con external_id (caso mayoritario) ---
    with_id = [p for p in scraped if p.external_id]
    without_id = [p for p in scraped if not p.external_id]

    existing_map: dict[str, SupermarketProduct] = {}
    if with_id:
        ids = [p.external_id for p in with_id]
        result = await session.execute(
            select(SupermarketProduct).where(
                SupermarketProduct.supermarket_id == supermarket.id,
                SupermarketProduct.external_id.in_(ids),
            )
        )
        existing_map = {sp.external_id: sp for sp in result.scalars().all()}

    for product in with_id:
        if product.external_id in existing_map:
            sp = existing_map[product.external_id]
            # Actualizar campos que pueden cambiar entre scrapes
            sp.name_raw = product.name_raw
            sp.url = product.url
            sp.image_url = product.image_url
            updated += 1
        else:
            sp = SupermarketProduct(
                supermarket_id=supermarket.id,
                external_id=product.external_id,
                name_raw=product.name_raw,
                url=product.url,
                image_url=product.image_url,
            )
            session.add(sp)
            # flush para obtener el ID antes de crear el registro de precio
            await session.flush()
            inserted += 1

        price_records.append(
            PriceHistory(
                supermarket_product_id=sp.id,
                price=product.price,
                currency=product.currency,
                date=today,
            )
        )

    # --- Productos sin external_id (fallback por nombre_raw) ---
    if without_id:
        logger.warning(
            "[%s] %d productos sin external_id — usando nombre_raw como fallback de deduplicación",
            supermarket.slug,
            len(without_id),
        )

    for product in without_id:
        result = await session.execute(
            select(SupermarketProduct).where(
                SupermarketProduct.supermarket_id == supermarket.id,
                SupermarketProduct.name_raw == product.name_raw,
                SupermarketProduct.external_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.url = product.url
            existing.image_url = product.image_url
            updated += 1
            sp = existing
        else:
            sp = SupermarketProduct(
                supermarket_id=supermarket.id,
                external_id=None,
                name_raw=product.name_raw,
                url=product.url,
                image_url=product.image_url,
            )
            session.add(sp)
            await session.flush()
            inserted += 1

        price_records.append(
            PriceHistory(
                supermarket_product_id=sp.id,
                price=product.price,
                currency=product.currency,
                date=today,
            )
        )

    session.add_all(price_records)
    return {"inserted": inserted, "updated": updated}


def _print_summary(
    save_stats: dict[str, dict],
    norm_stats: dict[str, int],
    elapsed_s: float,
) -> None:
    mins, secs = divmod(int(elapsed_s), 60)
    sep = "=" * 52

    print(f"\n{sep}")
    print("  RESUMEN DEL SCRAPE")
    print(sep)
    print(f"  Tiempo total: {mins}m {secs}s\n")

    for slug, stats in save_stats.items():
        if stats.get("error"):
            print(f"  {slug:22s}  ERROR — ver logs")
        else:
            print(
                f"  {slug:22s}  {stats['scraped']:5d} productos  "
                f"(nuevos: {stats['inserted']}  actualizados: {stats['updated']})"
            )

    processed = norm_stats["processed"] or 1  # evitar división por cero
    print(f"\n  Normalización ({norm_stats['processed']} productos procesados):")
    for label, key in [
        ("Auto-match (≥90):  ", "matched_auto"),
        ("Tentativo (70-89): ", "matched_tentative"),
        ("Nuevos canónicos:  ", "created_new"),
    ]:
        n = norm_stats[key]
        print(f"    {label}  {n:5d}  ({n / processed * 100:.1f}%)")

    print(sep)


# ---------------------------------------------------------------------------
# Punto de entrada como módulo ejecutable
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_all_scrapers())
