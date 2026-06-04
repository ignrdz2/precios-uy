"""Pipeline de normalización de productos.

Agrupa productos equivalentes entre supermercados bajo un producto canónico.

Ejemplo:
    "CONAPROLE LECHE ENTERA 1 LT"  (Disco)          →  product_id=42
    "Leche Conaprole Entera 1L"    (Tienda Inglesa)  →  product_id=42

El pipeline es idempotente: solo procesa supermarket_products con product_id IS NULL.
Se ejecuta después de cada scrape completo, nunca bloquea el scraping.
"""

import logging
import re
import unicodedata

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.product import Product
from app.models.supermarket_product import SupermarketProduct

logger = logging.getLogger(__name__)

# Umbrales de decisión según spec
_SCORE_AUTO = 90       # ≥ 90 → match automático
_SCORE_TENTATIVE = 70  # 70-89 → match tentativo (requiere revisión manual)
                       # < 70  → nuevo producto canónico


class ProductNormalizer:
    # Patrones de normalización de unidades: (regex, reemplazo)
    # El orden importa: las formas más largas van primero para evitar
    # que "litros" sea capturado parcialmente antes que "litro".
    _UNIT_SUBS: list[tuple[str, str]] = [
        (r"\b(\d+(?:[,.]\d+)?)\s*mililitros?\b", r"\1ml"),
        (r"\b(\d+(?:[,.]\d+)?)\s*ml\b",          r"\1ml"),
        (r"\b(\d+(?:[,.]\d+)?)\s*cc\b",          r"\1ml"),
        (r"\b(\d+(?:[,.]\d+)?)\s*litros?\b",      r"\1l"),
        (r"\b(\d+(?:[,.]\d+)?)\s*lts?\b",         r"\1l"),
        (r"\b(\d+(?:[,.]\d+)?)\s*kilogramos?\b",  r"\1kg"),
        (r"\b(\d+(?:[,.]\d+)?)\s*kilos?\b",       r"\1kg"),
        # "kg" ya es la forma canónica, solo normaliza el espacio
        (r"\b(\d+(?:[,.]\d+)?)\s*kg\b",           r"\1kg"),
        (r"\b(\d+(?:[,.]\d+)?)\s*gramos?\b",      r"\1g"),
        (r"\b(\d+(?:[,.]\d+)?)\s*grs?\b",         r"\1g"),
    ]

    # ------------------------------------------------------------------
    # Método principal
    # ------------------------------------------------------------------

    async def normalize_all(self, db_session: AsyncSession) -> dict[str, int]:
        """Normaliza todos los supermarket_products con product_id IS NULL.

        Retorna:
            {
                "processed":         número de productos procesados,
                "matched_auto":      asignados con score >= 90,
                "matched_tentative": asignados con score 70-89 (requieren revisión),
                "created_new":       nuevos productos canónicos creados,
            }
        """
        stats: dict[str, int] = {
            "processed": 0,
            "matched_auto": 0,
            "matched_tentative": 0,
            "created_new": 0,
        }

        # Cargar solo productos de supermercado sin normalizar (garantiza idempotencia)
        result = await db_session.execute(
            select(SupermarketProduct).where(SupermarketProduct.product_id.is_(None))
        )
        unmatched: list[SupermarketProduct] = list(result.scalars().all())

        if not unmatched:
            logger.info("[normalizer] ningún producto pendiente de normalizar")
            return stats

        logger.info("[normalizer] iniciando normalización de %d productos", len(unmatched))

        # Cargar canónicos existentes y construir índice preprocesado una sola vez
        result = await db_session.execute(select(Product))
        canonical_index: list[tuple[int, str]] = [
            (p.id, self._preprocess(p.name))
            for p in result.scalars().all()
        ]

        for sp in unmatched:
            preprocessed = self._preprocess(sp.name_raw)
            product_id, score = self._find_best_match(preprocessed, canonical_index)

            stats["processed"] += 1

            if score >= _SCORE_AUTO:
                # Match con alta confianza — asignar directamente
                sp.product_id = product_id
                stats["matched_auto"] += 1
                logger.debug(
                    "[normalizer] auto-match (%.0f): '%s' → product_id=%d",
                    score,
                    sp.name_raw,
                    product_id,
                )

            elif score >= _SCORE_TENTATIVE:
                # Match con confianza media — asignar pero alertar para revisión humana
                sp.product_id = product_id
                stats["matched_tentative"] += 1
                logger.warning(
                    "[normalizer] match tentativo (%.0f): '%s' → product_id=%d "
                    "[verificar manualmente si es correcto]",
                    score,
                    sp.name_raw,
                    product_id,
                )

            else:
                # Sin match suficiente — crear nuevo producto canónico
                new_product = Product(name=preprocessed)
                db_session.add(new_product)
                # flush para que SQLAlchemy asigne el ID antes de continuar el batch
                await db_session.flush()
                sp.product_id = new_product.id
                # Agregar al índice en memoria para que los siguientes productos
                # del mismo batch puedan matchear contra este nuevo canónico
                canonical_index.append((new_product.id, preprocessed))
                stats["created_new"] += 1
                logger.info(
                    "[normalizer] nuevo canónico (score=%.0f): '%s' → id=%d",
                    score,
                    sp.name_raw,
                    new_product.id,
                )

        # Commit único al final del batch entero, no por producto
        await db_session.commit()

        logger.info(
            "[normalizer] finalizado — procesados=%d  auto=%d  tentativo=%d  nuevos=%d",
            stats["processed"],
            stats["matched_auto"],
            stats["matched_tentative"],
            stats["created_new"],
        )
        return stats

    # ------------------------------------------------------------------
    # Preprocesamiento
    # ------------------------------------------------------------------

    @classmethod
    def _preprocess(cls, name: str) -> str:
        """Normaliza un nombre de producto para comparación fuzzy.

        Pasos:
        1. Minúsculas
        2. Eliminar tildes (é→e, á→a, etc.) para homogeneizar variantes
        3. Normalizar unidades de medida ("1 litro" → "1l", "500 gr" → "500g")
        4. Remover caracteres especiales; conservar solo letras, números y espacios
        5. Colapsar espacios múltiples y hacer strip

        Ejemplo:
            "CONAPROLE LECHE ENTERA 1 LT" → "conaprole leche entera 1l"
            "Leche Conaprole Entera 1L"   → "conaprole leche entera 1l"
        """
        # Paso 1: minúsculas
        text = name.lower()

        # Paso 2: eliminar tildes (normalización Unicode NFKD separa base + diacrítico)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))

        # Paso 3: normalizar unidades
        for pattern, replacement in cls._UNIT_SUBS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Paso 4: eliminar caracteres que no sean letras, números o espacios
        text = re.sub(r"[^a-z0-9\s]", " ", text)

        # Paso 5: colapsar espacios y strip
        return re.sub(r"\s+", " ", text).strip()

    # ------------------------------------------------------------------
    # Fuzzy matching
    # ------------------------------------------------------------------

    @staticmethod
    def _find_best_match(
        preprocessed: str,
        canonical_index: list[tuple[int, str]],
    ) -> tuple[int | None, float]:
        """Devuelve (product_id, score) del canónico más parecido.

        Usa token_sort_ratio de rapidfuzz, que es robusto al orden de palabras:
        "leche entera conaprole" y "conaprole leche entera" dan score 100.

        Si no hay canónicos, devuelve (None, 0.0).
        """
        if not canonical_index:
            return None, 0.0

        names = [name for _, name in canonical_index]
        result = process.extractOne(
            preprocessed,
            names,
            scorer=fuzz.token_sort_ratio,
        )
        if result is None:
            return None, 0.0

        _best_name, score, idx = result
        product_id = canonical_index[idx][0]
        return product_id, float(score)


# ------------------------------------------------------------------
# Función standalone para testing y ejecución manual
# ------------------------------------------------------------------


async def normalize_all_standalone() -> dict[str, int]:
    """Crea su propia sesión de DB y corre el pipeline completo.

    Uso:
        docker compose exec backend python -c "
        import asyncio
        from app.services.normalizer import normalize_all_standalone
        print(asyncio.run(normalize_all_standalone()))
        "
    """
    async with AsyncSessionLocal() as session:
        normalizer = ProductNormalizer()
        return await normalizer.normalize_all(session)
