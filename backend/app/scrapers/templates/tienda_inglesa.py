"""Scraper para Tienda Inglesa (https://www.tinglesa.com.uy).

El sitio es una SPA con JavaScript (plataforma VTEX), por lo que toda la
extracción se hace con Playwright — no requests/httpx.

AVISO SOBRE SELECTORES CSS
---------------------------
Cada constante con prefijo _SEL_ es una suposición basada en la plataforma
VTEX (común en supermercados uruguayos). Deben verificarse contra el sitio
real antes de que este scraper pueda producir datos reales.

Cómo verificar:
  1. Abrir https://www.tinglesa.com.uy en Chrome DevTools
  2. Navegar a cualquier página de listado de categoría
  3. Inspeccionar una tarjeta de producto y mapear el elemento a cada
     constante _SEL_* de abajo
  4. Reemplazar el selector provisional y eliminar el comentario TODO
"""

import asyncio
import logging
import re

from playwright.async_api import Locator, Page, async_playwright

from .base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)

_PAGE_TIMEOUT_MS = 30_000
_SCROLL_PAUSE_S = 1.5
_MAX_LOAD_ITERATIONS = 80  # tope de seguridad: ~80 × 20 productos ≈ 1.600 por categoría


class TiendaInglesaScraper(BaseScraper):
    BASE_URL = "https://www.tinglesa.com.uy"

    # ------------------------------------------------------------------
    # URLs de categorías
    # TODO: Abrir tinglesa.com.uy, navegar por el menú principal y copiar
    #       el href exacto de cada categoría para verificar/reemplazar abajo.
    # ------------------------------------------------------------------
    CATEGORIES: dict[str, str] = {
        "lacteos": f"{BASE_URL}/lacteos-y-huevos",
        "carnes": f"{BASE_URL}/carnes-y-aves",
        "verduras": f"{BASE_URL}/frutas-y-verduras",
        "bebidas": f"{BASE_URL}/bebidas",
        "limpieza": f"{BASE_URL}/limpieza-del-hogar",
    }

    # ------------------------------------------------------------------
    # Selectores CSS — todos requieren verificación en el navegador
    # (ver docstring del módulo para instrucciones)
    # ------------------------------------------------------------------

    # Contenedor principal de una tarjeta de producto en el grid
    # TODO: Inspeccionar el grid de productos; clase VTEX común: "vtex-product-summary-2-x-container"
    _SEL_PRODUCT_CARD = ".vtex-product-summary-2-x-container"

    # Elemento de texto con el nombre del producto dentro de la tarjeta
    # TODO: Verificar; clase VTEX común: "vtex-product-summary-2-x-productNameContainer"
    _SEL_PRODUCT_NAME = ".vtex-product-summary-2-x-productNameContainer"

    # Precio de venta (precio final/con descuento, no el precio tachado)
    # TODO: Verificar; clases VTEX comunes: "vtex-product-price-1-x-sellingPriceValue"
    #       o ".sellingPrice .vtex-product-price-1-x-currencyContainer"
    _SEL_PRODUCT_PRICE = ".vtex-product-price-1-x-sellingPriceValue"

    # Etiqueta <a> cuyo href lleva a la página de detalle del producto
    # TODO: Verificar; clase VTEX común: "vtex-product-summary-2-x-clearLink"
    _SEL_PRODUCT_LINK = "a.vtex-product-summary-2-x-clearLink"

    # Etiqueta <img> de la miniatura del producto
    # TODO: Verificar; algunas implementaciones de lazy-load usan data-src en lugar de src
    _SEL_PRODUCT_IMAGE = ".vtex-product-summary-2-x-image img"

    # Botón "Cargar más" / "Ver más" (solo presente si el sitio usa carga manual)
    # TODO: Verificar; puede estar ausente si el sitio usa scroll infinito automático
    _SEL_LOAD_MORE_BTN = "button.vtex-button--primary[class*='load-more'], button[data-testid='show-more']"

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
                    logger.info(
                        "[tienda_inglesa] %s → %d productos", category_name, len(products)
                    )
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

        # Navegar; si networkidle expira, reintentar con 'load'
        try:
            await page.goto(url, wait_until="networkidle")
        except Exception:
            logger.warning(
                "[tienda_inglesa] timeout de networkidle en %s, reintentando con 'load'", url
            )
            await page.goto(url, wait_until="load")

        # TODO: Si el sitio muestra un banner de cookies/GDPR en la primera visita,
        #       agregar un click aquí para cerrarlo antes de contar tarjetas de producto.
        #       Ejemplo: await page.locator("button[data-testid='accept-cookies']").click()

        # Esperar a que aparezca al menos una tarjeta de producto antes de continuar
        try:
            await page.wait_for_selector(self._SEL_PRODUCT_CARD, timeout=_PAGE_TIMEOUT_MS)
        except Exception:
            logger.warning(
                "[tienda_inglesa] no se encontraron tarjetas en %s — selector posiblemente incorrecto: %s",
                url,
                self._SEL_PRODUCT_CARD,
            )
            return []

        await self._load_all_products(page)
        return await self._extract_products(page, category_name)

    # ------------------------------------------------------------------
    # Paginación / scroll infinito
    # ------------------------------------------------------------------

    async def _load_all_products(self, page: Page) -> None:
        """Expande la página hasta que todos los productos estén visibles.

        Estrategia: si hay un botón 'cargar más', hace clic repetidamente.
        De lo contrario, usa scroll infinito.
        """
        # TODO: Determinar qué estrategia usa el sitio real y eliminar la otra.
        load_more_btn = page.locator(self._SEL_LOAD_MORE_BTN)
        if await load_more_btn.count() > 0 and await load_more_btn.first.is_visible():
            await self._click_load_more_until_exhausted(page, load_more_btn)
        else:
            await self._scroll_until_stable(page)

    async def _click_load_more_until_exhausted(
        self, page: Page, btn: Locator
    ) -> None:
        """Hace clic en 'cargar más' hasta que desaparece o deja de agregar productos."""
        for _ in range(_MAX_LOAD_ITERATIONS):
            if not (await btn.count() > 0 and await btn.first.is_visible()):
                break
            prev_count = await page.locator(self._SEL_PRODUCT_CARD).count()
            try:
                await btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
            except Exception as exc:
                logger.warning("[tienda_inglesa] fallo al hacer clic en 'cargar más': %s", exc)
                break
            new_count = await page.locator(self._SEL_PRODUCT_CARD).count()
            if new_count == prev_count:
                break  # botón clickeado pero no se cargaron productos nuevos — llegamos al final

    async def _scroll_until_stable(self, page: Page) -> None:
        """Hace scroll al final de la página repetidamente hasta que el conteo de productos no cambia."""
        prev_count = 0
        for _ in range(_MAX_LOAD_ITERATIONS):
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
        logger.debug(
            "[tienda_inglesa] parseando %d tarjetas para categoría '%s'", total, category_name
        )

        products: list[ScrapedProduct] = []
        for i in range(total):
            try:
                product = await self._parse_card(cards.nth(i), category_name)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.warning(
                    "[tienda_inglesa] fallo al parsear tarjeta %d en '%s': %s",
                    i,
                    category_name,
                    exc,
                )
        return products

    async def _parse_card(self, card: Locator, category_name: str) -> ScrapedProduct | None:
        # ---- nombre --------------------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_NAME contra una tarjeta real del sitio
        name_el = card.locator(self._SEL_PRODUCT_NAME)
        if await name_el.count() == 0:
            logger.warning("[tienda_inglesa] tarjeta sin elemento de nombre — omitiendo")
            return None
        name_raw = (await name_el.first.inner_text()).strip()
        if not name_raw:
            return None

        # ---- precio --------------------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_PRICE; asegurarse de que apunta al precio
        #       de venta y no al precio original tachado.
        price_el = card.locator(self._SEL_PRODUCT_PRICE)
        if await price_el.count() == 0:
            logger.warning(
                "[tienda_inglesa] sin elemento de precio para '%s' — omitiendo", name_raw
            )
            return None
        price_raw = (await price_el.first.inner_text()).strip()
        price = self._parse_price(price_raw)
        if price is None:
            logger.warning(
                "[tienda_inglesa] no se puede parsear precio '%s' para '%s' — omitiendo",
                price_raw,
                name_raw,
            )
            return None

        # ---- URL del producto -----------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_LINK
        link_el = card.locator(self._SEL_PRODUCT_LINK)
        href: str | None = None
        if await link_el.count() > 0:
            href = await link_el.first.get_attribute("href")
        if href:
            product_url = href if href.startswith("http") else self.BASE_URL + href
        else:
            # URL desconocida pero tenemos nombre y precio — conservar el producto
            product_url = self.BASE_URL
            logger.warning("[tienda_inglesa] sin href para '%s'", name_raw)

        # ---- external_id ---------------------------------------------------
        # TODO: Inspeccionar el elemento card en DevTools.
        # VTEX almacena el ID del producto en data-product-id o data-sku-id en el
        # elemento raíz de la tarjeta; verificar cuál atributo está presente.
        external_id = (
            await card.get_attribute("data-product-id")
            or await card.get_attribute("data-sku-id")
            or await card.get_attribute("data-id")
        )
        if not external_id and href:
            # Fallback: usar el último segmento de la URL (slug o ID numérico)
            external_id = href.rstrip("/").split("/")[-1].split("?")[0] or None

        # ---- imagen --------------------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_IMAGE; revisar si hay lazy-load (data-src vs src)
        image_url: str | None = None
        img_el = card.locator(self._SEL_PRODUCT_IMAGE)
        if await img_el.count() > 0:
            image_url = await img_el.first.get_attribute("src") or await img_el.first.get_attribute(
                "data-src"
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
        """Convierte un string de precio uruguayo a float.

        Formatos soportados:
            "$1.299"      → 1299.0
            "$ 1.299,90"  → 1299.9
            "1.299,00"    → 1299.0
            "1299"        → 1299.0
        """
        # Eliminar símbolo de moneda, espacios y caracteres no numéricos excepto . y ,
        cleaned = re.sub(r"[^\d.,]", "", raw).strip()
        if not cleaned:
            return None

        if "," in cleaned:
            # Formato UY con coma decimal: "1.299,90" → "1299.90"
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Sin coma decimal: los puntos son separadores de miles — "1.299" → "1299"
            cleaned = cleaned.replace(".", "")

        try:
            value = float(cleaned)
        except ValueError:
            return None

        return value if value > 0 else None

    @staticmethod
    def _category_name_from_url(url: str) -> str:
        """Obtiene un nombre de categoría legible a partir de un segmento de URL."""
        segment = url.rstrip("/").split("/")[-1]
        return segment.replace("-", " ").replace("_", " ").title()
