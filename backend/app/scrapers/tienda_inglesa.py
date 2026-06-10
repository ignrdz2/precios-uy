"""Scraper para Tienda Inglesa (https://www.tiendainglesa.com.uy).

El sitio es una plataforma propietaria GeneXus.
Usa scroll infinito — comienza con 40 productos y carga más al bajar.

Selectores verificados en producción (junio 2026) inspeccionando el DOM real.
"""

import asyncio
import logging
import re

from playwright.async_api import Locator, Page, async_playwright

from .base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)

_PAGE_TIMEOUT_MS  = 30_000
_SCROLL_PAUSE_S   = 2.0
_MAX_SCROLL_ITERS = 120  # tope de seguridad para scroll infinito


class TiendaInglesaScraper(BaseScraper):
    BASE_URL = "https://www.tiendainglesa.com.uy"

    CATEGORIES: dict[str, str] = {
        "lacteos":  f"{BASE_URL}/supermercado/categoria/frescos/lacteos/busqueda?0,0,*%3A*,1894,209,0,rel,,false,,,,0",
        "carnes":   f"{BASE_URL}/supermercado/categoria/frescos/carnes/busqueda?0,0,*%3A*,1894,173,0,rel,,false,,,,0",
        "verduras": f"{BASE_URL}/supermercado/categoria/frescos/verduras/busqueda?0,0,*%3A*,1894,196,0,rel,,false,,,,0",
        "frutas":   f"{BASE_URL}/supermercado/categoria/frescos/frutas/busqueda?0,0,*%3A*,1894,195,0,rel,,false,,,,0",
        "bebidas":  f"{BASE_URL}/supermercado/categoria/bebidas/1001",
        "limpieza": f"{BASE_URL}/supermercado/categoria/limpieza/1895",
    }

    # Selectores verificados contra el DOM real del sitio
    _SEL_PRODUCT_CARD  = ".card-product-container"
    _SEL_PRODUCT_NAME  = ".card-product-name"
    # .ProductPrice apunta siempre al precio de venta vigente (ignora .wTxtProductPriceBefore)
    _SEL_PRODUCT_PRICE = ".ProductPrice"
    _SEL_PRODUCT_LINK  = ".card-product-container a"
    # lazysizes: la imagen usa data-src antes de entrar al viewport
    _SEL_PRODUCT_IMAGE = ".card-product-img"

    def __init__(self) -> None:
        super().__init__(supermarket_slug="tienda_inglesa")

    # ------------------------------------------------------------------
    # Interfaz pública (implementa BaseScraper)
    # ------------------------------------------------------------------

    async def scrape_all(self) -> list[ScrapedProduct]:
        """Abre una sesión de browser y scrapea todas las categorías configuradas."""
        logger.info("[tienda_inglesa] iniciando scrape completo (%d categorías)", len(self.CATEGORIES))
        all_products: list[ScrapedProduct] = []

        async with async_playwright() as playwright:
            context = await self._get_browser_context(playwright)
            page = await context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(_PAGE_TIMEOUT_MS)

            try:
                for category_name, category_url in self.CATEGORIES.items():
                    products = await self._safe_scrape_with_retry(
                        lambda u=category_url, n=category_name: self._scrape_category_page(
                            page, u, n
                        )
                    )
                    logger.info("[tienda_inglesa] %s → %d productos", category_name, len(products))
                    all_products.extend(products)
                    await self._random_delay()
            finally:
                await context.close()

        logger.info("[tienda_inglesa] finalizado — total productos: %d", len(all_products))
        return all_products

    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        """Scrapea una URL de categoría de forma standalone (abre su propio browser)."""
        category_name = self._category_name_from_url(category_url)
        async with async_playwright() as playwright:
            context = await self._get_browser_context(playwright)
            page = await context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(_PAGE_TIMEOUT_MS)
            try:
                return await self._scrape_category_page(page, category_url, category_name)
            finally:
                await context.close()

    # ------------------------------------------------------------------
    # Scraping a nivel de página
    # ------------------------------------------------------------------

    async def _scrape_category_page(
        self, page: Page, url: str, category_name: str
    ) -> list[ScrapedProduct]:
        logger.debug("[tienda_inglesa] → %s", url)

        try:
            await page.goto(url, wait_until="networkidle")
        except Exception:
            logger.warning("[tienda_inglesa] timeout networkidle en %s, reintentando con 'load'", url)
            await page.goto(url, wait_until="load")

        try:
            await page.wait_for_selector(self._SEL_PRODUCT_CARD, timeout=_PAGE_TIMEOUT_MS)
        except Exception:
            logger.warning(
                "[tienda_inglesa] sin tarjetas en %s — selector: %s",
                url,
                self._SEL_PRODUCT_CARD,
            )
            return []

        await self._scroll_until_stable(page)
        return await self._extract_products(page, category_name)

    # ------------------------------------------------------------------
    # Scroll infinito
    # ------------------------------------------------------------------

    async def _scroll_until_stable(self, page: Page) -> None:
        """Hace scroll hasta que no aparecen productos nuevos.

        El sitio carga en bloques: comienza con 40 y suma más al bajar.
        """
        prev_count = 0
        for _ in range(_MAX_SCROLL_ITERS):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(_SCROLL_PAUSE_S)
            current_count = await page.locator(self._SEL_PRODUCT_CARD).count()
            if current_count == prev_count:
                break
            prev_count = current_count

    # ------------------------------------------------------------------
    # Extracción de productos
    # ------------------------------------------------------------------

    async def _extract_products(self, page: Page, category_name: str) -> list[ScrapedProduct]:
        cards = page.locator(self._SEL_PRODUCT_CARD)
        total = await cards.count()
        logger.debug("[tienda_inglesa] parseando %d tarjetas — '%s'", total, category_name)

        products: list[ScrapedProduct] = []
        for i in range(total):
            try:
                product = await self._parse_card(cards.nth(i), category_name)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.warning(
                    "[tienda_inglesa] fallo al parsear tarjeta %d en '%s': %s",
                    i, category_name, exc,
                )
        return products

    async def _parse_card(self, card: Locator, category_name: str) -> ScrapedProduct | None:
        # ---- nombre --------------------------------------------------------
        name_el = card.locator(self._SEL_PRODUCT_NAME)
        if await name_el.count() == 0:
            return None
        name_raw = (await name_el.first.inner_text()).strip()
        if not name_raw:
            return None

        # ---- precio --------------------------------------------------------
        # .ProductPrice apunta al precio de venta; ignora el tachado (.wTxtProductPriceBefore)
        # y el precio con ClubCard (.ProductSpecialPrice)
        price_el = card.locator(self._SEL_PRODUCT_PRICE)
        if await price_el.count() == 0:
            logger.warning("[tienda_inglesa] sin precio para '%s' — omitiendo", name_raw)
            return None
        price_raw = (await price_el.first.inner_text()).strip()
        price = self._parse_price(price_raw)
        if price is None:
            logger.warning(
                "[tienda_inglesa] precio no parseable '%s' para '%s' — omitiendo",
                price_raw, name_raw,
            )
            return None

        # ---- URL y external_id ---------------------------------------------
        # data-id en el contenedor es el product ID (más estable que el SKU/id del elemento)
        external_id: str | None = await card.get_attribute("data-id")

        link_el = card.locator(self._SEL_PRODUCT_LINK)
        href: str | None = None
        if await link_el.count() > 0:
            href = await link_el.first.get_attribute("href")

        if href:
            product_url = href if href.startswith("http") else self.BASE_URL + href
        else:
            product_url = self.BASE_URL
            logger.warning("[tienda_inglesa] sin href para '%s'", name_raw)

        # ---- imagen --------------------------------------------------------
        # lazysizes: usar data-src como fuente primaria (src se llena al entrar al viewport)
        image_url: str | None = None
        img_el = card.locator(self._SEL_PRODUCT_IMAGE)
        if await img_el.count() > 0:
            image_url = (
                await img_el.first.get_attribute("data-src")
                or await img_el.first.get_attribute("src")
            )

        return ScrapedProduct(
            external_id=external_id,
            name_raw=name_raw,
            price=price,
            currency="UYU",
            url=product_url,
            image_url=image_url,
            category=category_name,
        )

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_price(raw: str) -> float | None:
        """Convierte strings de precio uruguayo a float.

        Formatos soportados:
            "$ 125"       → 125.0
            "$ 1.299"     → 1299.0
            "$ 101,15"    → 101.15
            "$ 1.299,90"  → 1299.9
        """
        cleaned = re.sub(r"[^\d.,]", "", raw).strip()
        if not cleaned:
            return None

        if "," in cleaned:
            # Coma como decimal; punto (si aparece) es separador de miles
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            dot_pos = cleaned.rfind(".")
            if dot_pos != -1 and len(cleaned) - dot_pos - 1 == 3:
                cleaned = cleaned.replace(".", "")  # "1.299" → miles

        try:
            value = float(cleaned)
        except ValueError:
            return None

        return value if value > 0 else None

    @staticmethod
    def _category_name_from_url(url: str) -> str:
        """Extrae el slug de categoría legible de una URL de tiendainglesa.com.uy.

        /supermercado/categoria/frescos/lacteos/busqueda?... → "Lacteos"
        /supermercado/categoria/bebidas/1001               → "Bebidas"
        """
        # Tomar la parte antes del query string
        path = url.split("?")[0].rstrip("/")
        parts = path.split("/")
        # Ignorar segmentos reservados y el ID numérico final
        reserved = {"", "supermercado", "categoria", "busqueda", "categoria"}
        for segment in reversed(parts):
            if not segment.isdigit() and segment not in reserved:
                return segment.replace("-", " ").replace("_", " ").title()
        return parts[-1]


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    async def _main() -> None:
        scraper = TiendaInglesaScraper()
        products = await scraper.scrape_all()
        print(f"\nProductos encontrados: {len(products)}")
        for p in products[:5]:
            print(f"  {p.name_raw!r:50s}  ${p.price:.2f}  [{p.category}]")
        if len(products) > 5:
            print(f"  ... y {len(products) - 5} más")

    asyncio.run(_main())
