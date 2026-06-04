"""Scraper para Disco Uruguay (https://www.disco.com.uy).

El sitio es una SPA React con carga de productos vía JavaScript,
por lo que toda la extracción se hace con Playwright — no requests/httpx.

AVISO SOBRE SELECTORES CSS
---------------------------
Cada constante con prefijo _SEL_ es una suposición basada en patrones comunes
de e-commerce React (Cencosud/Disco UY). Deben verificarse contra el sitio
real antes de que este scraper pueda producir datos reales.

Cómo verificar:
  1. Abrir https://www.disco.com.uy en Chrome DevTools
  2. Navegar a cualquier página de listado de categoría
  3. Inspeccionar una tarjeta de producto y mapear el elemento a cada
     constante _SEL_* de abajo
  4. Reemplazar el selector provisional y eliminar el comentario TODO

Formato de precio observado: "$ 89,90" → 89.90
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


class DiscoScraper(BaseScraper):
    BASE_URL = "https://www.disco.com.uy"

    # ------------------------------------------------------------------
    # URLs de categorías
    # TODO: Abrir disco.com.uy, navegar por el menú principal y copiar
    #       el href exacto de cada categoría para verificar/reemplazar abajo.
    #       Disco puede usar rutas tipo /categoria/slug o /?q=slug&map=c
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
    # TODO: Inspeccionar el grid; posibles valores: ".product-summary",
    #       "[data-testid='product-summary']", ".shelf-item", ".product-item"
    _SEL_PRODUCT_CARD = "[data-testid='product-summary']"

    # Elemento de texto con el nombre del producto dentro de la tarjeta
    # TODO: Verificar; posibles valores: ".product-summary-name",
    #       "[data-testid='product-name']", "h3.product-name", ".shelf-item__title"
    _SEL_PRODUCT_NAME = "[data-testid='product-name']"

    # Precio de venta vigente (precio final, no el precio tachado/original)
    # TODO: Verificar; cuando hay descuento el sitio muestra dos precios:
    #       el original tachado y el de venta. Este selector debe apuntar
    #       al precio de venta. Posibles valores: ".sellingPrice",
    #       "[data-testid='price-selling']", ".product-selling-price",
    #       ".shelf-item__sell-price"
    _SEL_PRODUCT_PRICE = "[data-testid='price-selling']"

    # Etiqueta <a> cuyo href lleva a la página de detalle del producto
    # TODO: Verificar; posibles valores: "a.product-summary-container",
    #       "[data-testid='product-link']", ".shelf-item a"
    _SEL_PRODUCT_LINK = "a[data-testid='product-link']"

    # Etiqueta <img> de la miniatura del producto
    # TODO: Verificar; algunas implementaciones de lazy-load usan data-src
    #       en lugar de src. Posibles valores: ".product-summary-image img",
    #       "[data-testid='product-image'] img", ".shelf-item__image img"
    _SEL_PRODUCT_IMAGE = "[data-testid='product-image'] img"

    # Botón "Ver más" / "Cargar más" (presente si el sitio usa carga manual)
    # TODO: Verificar; puede estar ausente si usa scroll infinito automático.
    #       Posibles valores: "button.show-more", "[data-testid='show-more-button']",
    #       ".gallery-layout-container button"
    _SEL_LOAD_MORE_BTN = "button[data-testid='show-more-button']"

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
                        lambda u=category_url, n=category_name: self._scrape_category_page(
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
                return await self._scrape_category_page(page, category_url, category_name)
            finally:
                await context.close()

    # ------------------------------------------------------------------
    # Scraping a nivel de página
    # ------------------------------------------------------------------

    async def _scrape_category_page(
        self, page: Page, url: str, category_name: str
    ) -> list[ScrapedProduct]:
        logger.debug("[disco] → %s", url)

        # Navegar; si networkidle expira, reintentar con 'load'
        try:
            await page.goto(url, wait_until="networkidle")
        except Exception:
            logger.warning(
                "[disco] timeout de networkidle en %s, reintentando con 'load'", url
            )
            await page.goto(url, wait_until="load")

        # TODO: Si el sitio muestra un banner de cookies/GDPR en la primera visita,
        #       agregar un click aquí para cerrarlo antes de contar tarjetas de producto.
        #       Ejemplo: await page.locator("button[id='onetrust-accept-btn-handler']").click()

        # Esperar a que aparezca al menos una tarjeta de producto antes de continuar
        try:
            await page.wait_for_selector(self._SEL_PRODUCT_CARD, timeout=_PAGE_TIMEOUT_MS)
        except Exception:
            logger.warning(
                "[disco] no se encontraron tarjetas en %s — selector posiblemente incorrecto: %s",
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

        Estrategia: si hay un botón 'Ver más', hace clic repetidamente.
        De lo contrario, usa scroll infinito automático.
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
        """Hace clic en 'Ver más' hasta que desaparece o deja de agregar productos."""
        for _ in range(_MAX_LOAD_ITERATIONS):
            if not (await btn.count() > 0 and await btn.first.is_visible()):
                break
            prev_count = await page.locator(self._SEL_PRODUCT_CARD).count()
            try:
                await btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
            except Exception as exc:
                logger.warning("[disco] fallo al hacer clic en 'Ver más': %s", exc)
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
            "[disco] parseando %d tarjetas para categoría '%s'", total, category_name
        )

        products: list[ScrapedProduct] = []
        for i in range(total):
            try:
                product = await self._parse_card(cards.nth(i), category_name)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.warning(
                    "[disco] fallo al parsear tarjeta %d en '%s': %s",
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
            logger.warning("[disco] tarjeta sin elemento de nombre — omitiendo")
            return None
        name_raw = (await name_el.first.inner_text()).strip()
        if not name_raw:
            return None

        # ---- precio --------------------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_PRICE; asegurarse de que apunta al precio
        #       de venta vigente y no al precio original tachado.
        #       Disco muestra "$ 89,90" como precio final.
        price_el = card.locator(self._SEL_PRODUCT_PRICE)
        if await price_el.count() == 0:
            logger.warning(
                "[disco] sin elemento de precio para '%s' — omitiendo", name_raw
            )
            return None
        price_raw = (await price_el.first.inner_text()).strip()
        price = self._parse_price(price_raw)
        if price is None:
            logger.warning(
                "[disco] no se puede parsear precio '%s' para '%s' — omitiendo",
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
            logger.warning("[disco] sin href para '%s'", name_raw)

        # ---- external_id ---------------------------------------------------
        # TODO: Inspeccionar el elemento card en DevTools para encontrar el
        #       atributo que almacena el ID de producto del sitio.
        #       Posibles atributos: data-product-id, data-sku, data-item-id,
        #       o un atributo específico de la plataforma de Disco.
        external_id = (
            await card.get_attribute("data-product-id")
            or await card.get_attribute("data-sku")
            or await card.get_attribute("data-item-id")
        )
        if not external_id and href:
            # Fallback: usar el último segmento de la URL (slug o ID numérico)
            external_id = href.rstrip("/").split("/")[-1].split("?")[0] or None

        # ---- imagen --------------------------------------------------------
        # TODO: Verificar _SEL_PRODUCT_IMAGE; revisar si hay lazy-load (data-src vs src)
        image_url: str | None = None
        img_el = card.locator(self._SEL_PRODUCT_IMAGE)
        if await img_el.count() > 0:
            image_url = (
                await img_el.first.get_attribute("src")
                or await img_el.first.get_attribute("data-src")
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
            "$ 89,90"     → 89.9
            "$ 1.299,90"  → 1299.9
            "$89,90"      → 89.9
            "1299"        → 1299.0
            "89.90"       → 89.9   (punto como decimal, sin coma)
        """
        # Eliminar símbolo de moneda, espacios y caracteres no numéricos excepto . y ,
        cleaned = re.sub(r"[^\d.,]", "", raw).strip()
        if not cleaned:
            return None

        if "," in cleaned:
            # Formato UY con coma decimal: "1.299,90" → "1299.90"
            # El punto (si está) es separador de miles; la coma es decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Sin coma decimal: el punto puede ser decimal (89.90) o separador
            # de miles (1.299). Se distingue por posición: si hay exactamente
            # 3 dígitos después del punto, es separador de miles.
            dot_pos = cleaned.rfind(".")
            if dot_pos != -1 and len(cleaned) - dot_pos - 1 == 3:
                # "1.299" → separador de miles → "1299"
                cleaned = cleaned.replace(".", "")
            # else: "89.90" → decimal → dejar como está

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
