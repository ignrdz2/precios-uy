"""Scraper para Tienda Inglesa (https://www.tiendainglesa.com.uy).

El sitio es una plataforma propietaria GeneXus con paginación clásica.
Cada página muestra 40 productos. No usa scroll infinito — el DOM tiene
botones de página y la URL sigue el patrón:
  Pág 1: /supermercado/categoria/{slug}/{catId}
  Pág N: /supermercado/categoria/{slug}/busqueda?...,{catId},...,,,,{N-1}

Selectores verificados en producción (junio 2026) inspeccionando el DOM real.
"""

import asyncio
import logging
import math
import re

from playwright.async_api import Locator, Page, async_playwright

from .base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)

_PAGE_TIMEOUT_MS   = 30_000
_PRODUCTS_PER_PAGE = 40
_MAX_PAGES         = 80   # tope de seguridad: 80 × 40 = 3.200 productos por categoría


class TiendaInglesaScraper(BaseScraper):
    BASE_URL = "https://www.tiendainglesa.com.uy"

    # Cada entrada: (url_primera_página, prefijo_url_paginada)
    # Páginas 2+: prefijo + str(page_index)  donde page_index es 1, 2, 3, ...
    CATEGORIES: dict[str, tuple[str, str]] = {
        "lacteos": (
            f"{BASE_URL}/supermercado/categoria/frescos/lacteos/busqueda?0,0,*%3A*,1894,209,0,rel,,false,,,,0",
            f"{BASE_URL}/supermercado/categoria/frescos/lacteos/busqueda?0,0,*%3A*,1894,209,0,rel,,false,,,,",
        ),
        "carnes": (
            f"{BASE_URL}/supermercado/categoria/frescos/carnes/busqueda?0,0,*%3A*,1894,173,0,rel,,false,,,,0",
            f"{BASE_URL}/supermercado/categoria/frescos/carnes/busqueda?0,0,*%3A*,1894,173,0,rel,,false,,,,",
        ),
        "verduras": (
            f"{BASE_URL}/supermercado/categoria/frescos/verduras/busqueda?0,0,*%3A*,1894,196,0,rel,,false,,,,0",
            f"{BASE_URL}/supermercado/categoria/frescos/verduras/busqueda?0,0,*%3A*,1894,196,0,rel,,false,,,,",
        ),
        "frutas": (
            f"{BASE_URL}/supermercado/categoria/frescos/frutas/busqueda?0,0,*%3A*,1894,195,0,rel,,false,,,,0",
            f"{BASE_URL}/supermercado/categoria/frescos/frutas/busqueda?0,0,*%3A*,1894,195,0,rel,,false,,,,",
        ),
        "bebidas": (
            f"{BASE_URL}/supermercado/categoria/bebidas/1001",
            f"{BASE_URL}/supermercado/categoria/bebidas/busqueda?0,0,*%3A*,1001,0,0,,,false,,,,",
        ),
        "limpieza": (
            f"{BASE_URL}/supermercado/categoria/limpieza/1895",
            f"{BASE_URL}/supermercado/categoria/limpieza/busqueda?0,0,*%3A*,1895,0,0,,,false,,,,",
        ),
    }

    # Selectores verificados contra el DOM real del sitio
    _SEL_PRODUCT_CARD  = ".card-product-container"
    _SEL_PRODUCT_NAME  = ".card-product-name"
    # .ProductPrice apunta siempre al precio de venta vigente (ignora .wTxtProductPriceBefore)
    _SEL_PRODUCT_PRICE = ".ProductPrice"
    # lazysizes: la imagen usa data-src antes de entrar al viewport
    _SEL_PRODUCT_IMAGE = ".card-product-img"

    def __init__(self) -> None:
        super().__init__(supermarket_slug="tienda_inglesa")

    # ------------------------------------------------------------------
    # Interfaz pública (implementa BaseScraper)
    # ------------------------------------------------------------------

    async def scrape_all(self) -> list[ScrapedProduct]:
        """Abre una sesión de browser y scrapea todas las categorías."""
        logger.info("[tienda_inglesa] iniciando scrape completo (%d categorías)", len(self.CATEGORIES))
        all_products: list[ScrapedProduct] = []

        async with async_playwright() as playwright:
            context = await self._get_browser_context(playwright)
            page = await context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(_PAGE_TIMEOUT_MS)

            try:
                for cat_name, (first_url, page_tmpl) in self.CATEGORIES.items():
                    products = await self._safe_scrape_with_retry(
                        lambda u=first_url, t=page_tmpl, n=cat_name:
                            self._scrape_category_all_pages(page, n, u, t)
                    )
                    logger.info("[tienda_inglesa] %s → %d productos", cat_name, len(products))
                    all_products.extend(products)
                    await self._random_delay()
            finally:
                await context.close()

        logger.info("[tienda_inglesa] finalizado — total: %d productos", len(all_products))
        return all_products

    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        """Scrapea una categoría completa dado su URL de primera página."""
        cat_name = self._category_name_from_url(category_url)
        page_tmpl: str | None = None
        for name, (first_url, tmpl) in self.CATEGORIES.items():
            if first_url == category_url:
                cat_name = name
                page_tmpl = tmpl
                break

        async with async_playwright() as playwright:
            context = await self._get_browser_context(playwright)
            page = await context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(_PAGE_TIMEOUT_MS)
            try:
                if page_tmpl:
                    return await self._scrape_category_all_pages(page, cat_name, category_url, page_tmpl)
                return await self._scrape_single_page(page, category_url, cat_name)
            finally:
                await context.close()

    # ------------------------------------------------------------------
    # Iteración por páginas
    # ------------------------------------------------------------------

    async def _scrape_category_all_pages(
        self, page: Page, cat_name: str, first_url: str, page_tmpl: str
    ) -> list[ScrapedProduct]:
        """Itera todas las páginas de una categoría usando el contador 'N - M DE TOTAL'."""
        all_products: list[ScrapedProduct] = []

        # Página 1 — también sirve para leer el total de ítems
        first_page = await self._scrape_single_page(page, first_url, cat_name)
        all_products.extend(first_page)

        if not first_page:
            return all_products

        total_items = await self._get_total_items(page)
        if total_items:
            total_pages = min(math.ceil(total_items / _PRODUCTS_PER_PAGE), _MAX_PAGES)
            logger.info(
                "[tienda_inglesa] %s — %d ítems → %d páginas",
                cat_name, total_items, total_pages,
            )
        else:
            total_pages = _MAX_PAGES
            logger.warning(
                "[tienda_inglesa] %s — contador no encontrado; iterando hasta %d págs.",
                cat_name, _MAX_PAGES,
            )

        # Páginas 2..total_pages: page_tmpl + str(page_index) con índice 1-based
        for page_idx in range(1, total_pages):
            url = f"{page_tmpl}{page_idx}"
            page_products = await self._scrape_single_page(page, url, cat_name)
            if not page_products:
                break
            all_products.extend(page_products)
            await self._random_delay()

        return all_products

    async def _scrape_single_page(
        self, page: Page, url: str, cat_name: str
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
            logger.debug("[tienda_inglesa] sin tarjetas en %s — fin de paginación", url)
            return []

        return await self._extract_products(page, cat_name)

    async def _get_total_items(self, page: Page) -> int | None:
        """Extrae el total de productos del texto '1 - 40 DE 2.014' en la página."""
        try:
            body_text = await page.evaluate("document.body.innerText")
            match = re.search(r'\b\d+\s*[-–]\s*\d+\s+DE\s+([\d.]+)', body_text)
            if match:
                return int(match.group(1).replace(".", ""))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Extracción de productos
    # ------------------------------------------------------------------

    async def _extract_products(self, page: Page, cat_name: str) -> list[ScrapedProduct]:
        cards = page.locator(self._SEL_PRODUCT_CARD)
        total = await cards.count()
        logger.debug("[tienda_inglesa] parseando %d tarjetas — '%s'", total, cat_name)

        products: list[ScrapedProduct] = []
        for i in range(total):
            try:
                product = await self._parse_card(cards.nth(i), cat_name)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.warning("[tienda_inglesa] fallo tarjeta %d en '%s': %s", i, cat_name, exc)
        return products

    async def _parse_card(self, card: Locator, cat_name: str) -> ScrapedProduct | None:
        # ---- nombre --------------------------------------------------------
        name_el = card.locator(self._SEL_PRODUCT_NAME)
        if await name_el.count() == 0:
            return None
        name_raw = (await name_el.first.inner_text()).strip()
        if not name_raw:
            return None

        # ---- precio --------------------------------------------------------
        # .ProductPrice apunta al precio de venta; ignora tachado y ClubCard
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
        # data-id del contenedor es el product ID (más estable que el SKU)
        external_id: str | None = await card.get_attribute("data-id")

        # El <a> puede ser hijo del contenedor O envolverlo desde afuera;
        # buscamos en ambas direcciones con JS para cubrir los dos casos.
        href: str | None = await card.evaluate("""el => {
            const child = el.querySelector('a[href]:not([href=""])');
            if (child) return child.getAttribute('href');
            let node = el.parentElement;
            while (node && node.tagName !== 'BODY') {
                if (node.tagName === 'A') {
                    const h = node.getAttribute('href');
                    if (h && h !== '' && !h.startsWith('javascript')) return h;
                }
                node = node.parentElement;
            }
            return null;
        }""")

        if href:
            product_url = href if href.startswith("http") else self.BASE_URL + href
        else:
            product_url = self.BASE_URL
            logger.warning("[tienda_inglesa] sin href para '%s'", name_raw)

        # ---- imagen --------------------------------------------------------
        # lazysizes: data-src es la fuente real antes de entrar al viewport
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
            category=cat_name,
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
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            dot_pos = cleaned.rfind(".")
            if dot_pos != -1 and len(cleaned) - dot_pos - 1 == 3:
                cleaned = cleaned.replace(".", "")

        try:
            value = float(cleaned)
        except ValueError:
            return None

        return value if value > 0 else None

    @staticmethod
    def _category_name_from_url(url: str) -> str:
        """Extrae el slug de categoría legible de una URL de tiendainglesa.com.uy."""
        path = url.split("?")[0].rstrip("/")
        parts = path.split("/")
        reserved = {"", "supermercado", "categoria", "busqueda"}
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
