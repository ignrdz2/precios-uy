"""Scraper para Disco Uruguay (https://www.disco.com.uy).

El sitio es una SPA Blazor Server que carga productos via SignalR (WebSocket).
La paginación es scroll infinito: cada scroll hasta el fondo del DOM
dispara un evento Blazor que renderiza 20 productos adicionales.
El parámetro ?page=N no funciona — el router Blazor lo ignora.

Selectores verificados en producción (junio 2026) inspeccionando el DOM real.
"""

import asyncio
import logging
import re

from playwright.async_api import Locator, Page, async_playwright

from .base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)

_PAGE_TIMEOUT_MS  = 30_000
_SCROLL_WAIT_SEC  = 4.0   # Blazor Server responde por SignalR en ~3-4 s por scroll
_MAX_SCROLL_ITERS = 25    # tope de seguridad: 25 × 20 = 500 productos por categoría
_MAX_STALE_ITERS  = 2     # scrolls consecutivos sin nuevos productos → fin de categoría


class DiscoScraper(BaseScraper):
    BASE_URL = "https://www.disco.com.uy"

    CATEGORIES: dict[str, str] = {
        "almacen":  f"{BASE_URL}/products/category/almacen/10",
        "bebidas":  f"{BASE_URL}/products/category/bebidas/11",
        "limpieza": f"{BASE_URL}/products/category/perfumeria-y-limpieza/12",
        "frescos":  f"{BASE_URL}/products/category/frescos/14",
        "mascotas": f"{BASE_URL}/products/category/mascotas/15",
    }

    # Selectores verificados contra el DOM real del sitio
    _SEL_PRODUCT_CARD  = ".product-item"
    _SEL_PRODUCT_NAME  = "h3 a"
    # Usar .last para manejar productos por peso:
    #   productos normales → un solo .product-prices → un solo .val
    #   productos por peso → dos .product-prices; el primero es precio/kg (ignorar),
    #                        el segundo es precio final por unidad (tomar)
    _SEL_PRODUCT_PRICE = ".product-prices .val"
    _SEL_PRODUCT_LINK  = "h3 a"
    _SEL_PRODUCT_IMAGE = "figure img"  # usa src directamente (no data-src)

    def __init__(self) -> None:
        super().__init__(supermarket_slug="disco")

    # ------------------------------------------------------------------
    # Interfaz pública (implementa BaseScraper)
    # ------------------------------------------------------------------

    async def scrape_all(self) -> list[ScrapedProduct]:
        """Abre una sesión de browser y scrapea todas las categorías configuradas."""
        logger.info("[disco] iniciando scrape completo (%d categorías)", len(self.CATEGORIES))
        all_products: list[ScrapedProduct] = []

        async with async_playwright() as playwright:
            context = await self._get_browser_context(playwright)
            page = await context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(_PAGE_TIMEOUT_MS)

            try:
                for category_name, category_url in self.CATEGORIES.items():
                    products = await self._safe_scrape_with_retry(
                        lambda u=category_url, n=category_name: self._scrape_category_all_pages(
                            page, u, n
                        )
                    )
                    logger.info("[disco] %s → %d productos", category_name, len(products))
                    all_products.extend(products)
                    await self._random_delay()
            finally:
                await context.close()

        logger.info("[disco] finalizado — total productos: %d", len(all_products))
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
                return await self._scrape_category_all_pages(page, category_url, category_name)
            finally:
                await context.close()

    # ------------------------------------------------------------------
    # Scroll infinito Blazor Server
    # ------------------------------------------------------------------

    async def _scrape_category_all_pages(
        self, page: Page, base_url: str, category_name: str
    ) -> list[ScrapedProduct]:
        """Carga la categoría completa mediante scroll infinito.

        El sitio usa Blazor Server + SignalR: cada scroll hasta el fondo del DOM
        dispara un evento Blazor que renderiza 20 productos adicionales (~4 s de
        latencia por respuesta del servidor). El parámetro ?page=N no tiene efecto.
        """
        logger.debug("[disco] cargando %s", base_url)

        try:
            await page.goto(base_url, wait_until="networkidle")
        except Exception:
            logger.warning("[disco] timeout networkidle en %s, reintentando con 'load'", base_url)
            await page.goto(base_url, wait_until="load")

        try:
            await page.wait_for_selector(self._SEL_PRODUCT_CARD, timeout=_PAGE_TIMEOUT_MS)
        except Exception:
            logger.debug("[disco] sin tarjetas en %s", base_url)
            return []

        stale = 0
        prev_count = 0

        for scroll_n in range(_MAX_SCROLL_ITERS):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(_SCROLL_WAIT_SEC)

            current = await page.locator(self._SEL_PRODUCT_CARD).count()
            logger.debug("[disco] %s — %d tarjetas (scroll %d)", category_name, current, scroll_n + 1)

            if current == prev_count:
                stale += 1
                if stale >= _MAX_STALE_ITERS:
                    logger.debug("[disco] %s — sin nuevos productos, fin de scroll", category_name)
                    break
            else:
                stale = 0
            prev_count = current

        return await self._extract_products(page, category_name)

    # ------------------------------------------------------------------
    # Extracción de productos
    # ------------------------------------------------------------------

    async def _extract_products(self, page: Page, category_name: str) -> list[ScrapedProduct]:
        cards = page.locator(self._SEL_PRODUCT_CARD)
        total = await cards.count()
        logger.debug("[disco] parseando %d tarjetas — '%s'", total, category_name)

        products: list[ScrapedProduct] = []
        for i in range(total):
            try:
                product = await self._parse_card(cards.nth(i), category_name)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.warning("[disco] fallo al parsear tarjeta %d en '%s': %s", i, category_name, exc)
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
        # .last maneja los dos casos:
        #   producto normal  → un solo .val → se toma ese
        #   producto por peso → dos .val (precio/kg + precio final) → se toma el último
        price_els = card.locator(self._SEL_PRODUCT_PRICE)
        if await price_els.count() == 0:
            logger.warning("[disco] sin precio para '%s' — omitiendo", name_raw)
            return None
        price_raw = (await price_els.last.inner_text()).strip()
        price = self._parse_price(price_raw)
        if price is None:
            logger.warning("[disco] precio no parseable '%s' para '%s' — omitiendo", price_raw, name_raw)
            return None

        # ---- URL y external_id ---------------------------------------------
        # El ID está en el último segmento del href: /product/nombre-slug/570128
        link_el = card.locator(self._SEL_PRODUCT_LINK)
        href: str | None = None
        if await link_el.count() > 0:
            href = await link_el.first.get_attribute("href")

        if href:
            product_url = href if href.startswith("http") else self.BASE_URL + href
            external_id: str | None = href.rstrip("/").split("/")[-1].split("?")[0] or None
        else:
            product_url = self.BASE_URL
            external_id = None
            logger.warning("[disco] sin href para '%s'", name_raw)

        # ---- imagen --------------------------------------------------------
        image_url: str | None = None
        img_el = card.locator(self._SEL_PRODUCT_IMAGE)
        if await img_el.count() > 0:
            image_url = await img_el.first.get_attribute("src")

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
            "109"        → 109.0
            "1.299"      → 1299.0
            "89,90"      → 89.9
            "1.299,90"   → 1299.9
        """
        cleaned = re.sub(r"[^\d.,]", "", raw).strip()
        if not cleaned:
            return None

        if "," in cleaned:
            # Coma como separador decimal; punto (si aparece) es separador de miles
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Sin coma: el punto es decimal (89.90) o miles (1.299)
            # Se distingue por la cantidad de dígitos tras el punto
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
        """Extrae el slug de categoría legible de una URL de disco.com.uy.

        /products/category/bebidas/11 → "Bebidas"
        """
        parts = url.rstrip("/").split("/")
        # El ID numérico es el último segmento; el slug es el anterior
        for segment in reversed(parts):
            if not segment.isdigit() and segment not in ("", "category", "products"):
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
        scraper = DiscoScraper()
        products = await scraper.scrape_all()
        print(f"\nProductos encontrados: {len(products)}")
        for p in products[:5]:
            print(f"  {p.name_raw!r:50s}  ${p.price:.2f}  [{p.category}]")
        if len(products) > 5:
            print(f"  ... y {len(products) - 5} más")

    asyncio.run(_main())
