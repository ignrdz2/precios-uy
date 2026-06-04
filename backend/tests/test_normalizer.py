"""Tests del pipeline de normalización.

Ejecutar desde el directorio backend/:
    pytest tests/test_normalizer.py -v

Los tests que prueban _preprocess y _find_best_match son síncronos y rápidos.
El test de integración del pipeline usa mocks de DB (no requiere PostgreSQL).
"""

import pytest

from app.services.normalizer import ProductNormalizer, _SCORE_AUTO, _SCORE_TENTATIVE


# ---------------------------------------------------------------------------
# Tests de preprocesamiento
# ---------------------------------------------------------------------------


class TestPreprocess:
    """Verifica que _preprocess normaliza correctamente los nombres."""

    def test_uppercase_a_minusculas_con_unidad_lt(self):
        """Caso central del spec: CONAPROLE LECHE ENTERA 1 LT → forma canónica."""
        result = ProductNormalizer._preprocess("LECHE ENTERA CONAPROLE 1 LT")
        assert result == "leche entera conaprole 1l"

    def test_mixed_case_con_unidad_ya_normalizada(self):
        """El otro lado del mismo par del spec."""
        result = ProductNormalizer._preprocess("Leche Conaprole Entera 1L")
        assert result == "leche conaprole entera 1l"

    def test_unidad_gramos_a_g(self):
        """'500 gramos' debe quedar como '500g'."""
        result = ProductNormalizer._preprocess("Azucar 500 gramos")
        assert "500g" in result

    def test_unidad_ml_con_espacio(self):
        """'900 ML' con espacio y mayúsculas debe quedar como '900ml'."""
        result = ProductNormalizer._preprocess("ACEITE GIRASOL 900 ML")
        assert "900ml" in result

    def test_tildes_eliminadas(self):
        """Las tildes deben removerse para homogeneizar variantes de escritura."""
        result = ProductNormalizer._preprocess("Lácteos Mantequilla")
        assert "lacteos" in result
        assert "mantequilla" in result

    def test_caracteres_especiales_removidos(self):
        """Paréntesis, guiones y símbolos no deben quedar en el resultado."""
        result = ProductNormalizer._preprocess("Producto (Light) - 500g / caja")
        assert "(" not in result
        assert ")" not in result
        assert "-" not in result
        assert "/" not in result

    def test_espacios_multiples_colapsados(self):
        """El resultado nunca debe tener más de un espacio consecutivo."""
        result = ProductNormalizer._preprocess("Leche   Entera   1L")
        assert "  " not in result
        assert result == result.strip()


# ---------------------------------------------------------------------------
# Tests de fuzzy matching
# ---------------------------------------------------------------------------


class TestFindBestMatch:
    """Verifica que _find_best_match devuelve el producto correcto con el score esperado."""

    def test_leche_conaprole_score_alto(self):
        """Mismo producto con orden de palabras diferente debe dar score >= 90."""
        p1 = ProductNormalizer._preprocess("LECHE ENTERA CONAPROLE 1 LT")
        p2 = ProductNormalizer._preprocess("Leche Conaprole Entera 1l")
        _, score = ProductNormalizer._find_best_match(p1, [(1, p2)])
        assert score >= _SCORE_AUTO, (
            f"Se esperaba score >= {_SCORE_AUTO} para el mismo producto, se obtuvo {score:.1f}"
        )

    def test_aceite_girasol_score_alto(self):
        """Variantes de mayúsculas y espaciado deben producir un match alto."""
        p1 = ProductNormalizer._preprocess("Aceite de Girasol 900ml")
        p2 = ProductNormalizer._preprocess("ACEITE GIRASOL 900 ML")
        _, score = ProductNormalizer._find_best_match(p1, [(1, p2)])
        assert score >= _SCORE_TENTATIVE, (
            f"Se esperaba score >= {_SCORE_TENTATIVE} para variante del mismo producto, "
            f"se obtuvo {score:.1f}"
        )

    def test_coca_cola_vs_pepsi_score_bajo(self):
        """Productos distintos de la misma categoría NO deben alcanzar el umbral de match."""
        p1 = ProductNormalizer._preprocess("Coca Cola 2.25L")
        p2 = ProductNormalizer._preprocess("Pepsi 2L")
        _, score = ProductNormalizer._find_best_match(p1, [(1, p2)])
        assert score < _SCORE_TENTATIVE, (
            f"Coca Cola y Pepsi no deben matchear (score esperado < {_SCORE_TENTATIVE}, "
            f"se obtuvo {score:.1f})"
        )

    def test_indice_vacio_devuelve_none_y_cero(self):
        """Si no hay canónicos, debe retornar (None, 0.0) sin lanzar excepción."""
        product_id, score = ProductNormalizer._find_best_match("cualquier producto", [])
        assert product_id is None
        assert score == 0.0

    def test_elige_el_mas_parecido_entre_varios_candidatos(self):
        """Debe devolver el product_id del canónico más similar, no el primero."""
        query = ProductNormalizer._preprocess("Leche Entera 1L")
        canonicals = [
            (10, ProductNormalizer._preprocess("Yogur Entero 200g")),
            (20, ProductNormalizer._preprocess("Leche Entera 1 litro")),  # mejor match
            (30, ProductNormalizer._preprocess("Jugo de Naranja 1L")),
        ]
        product_id, score = ProductNormalizer._find_best_match(query, canonicals)
        assert product_id == 20, f"Se esperaba product_id=20, se obtuvo {product_id}"
        assert score >= _SCORE_AUTO


# ---------------------------------------------------------------------------
# Test de integración del pipeline (con DB mockeada)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nuevo_canonico_creado_cuando_no_hay_match():
    """Cuando no existen canónicos, el pipeline debe crear uno nuevo con score < 70."""
    from unittest.mock import AsyncMock, MagicMock, patch

    normalizer = ProductNormalizer()
    mock_session = AsyncMock()

    # Primer execute: un supermarket_product sin normalizar
    sp_mock = MagicMock()
    sp_mock.name_raw = "Produto Totalmente Inexistente XYZ 9999"
    sp_mock.product_id = None

    result_unmatched = MagicMock()
    result_unmatched.scalars.return_value.all.return_value = [sp_mock]

    # Segundo execute: tabla products vacía (score será 0.0 < 70)
    result_canonicals = MagicMock()
    result_canonicals.scalars.return_value.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[result_unmatched, result_canonicals])
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    # El Product creado recibirá id=99 después del flush
    new_product_instance = MagicMock()
    new_product_instance.id = 99

    with patch("app.services.normalizer.Product", return_value=new_product_instance):
        stats = await normalizer.normalize_all(mock_session)

    assert stats["processed"] == 1
    assert stats["created_new"] == 1
    assert stats["matched_auto"] == 0
    assert stats["matched_tentative"] == 0
    # El supermarket_product debe apuntar al nuevo canónico
    assert sp_mock.product_id == 99
    # Debe haberse hecho exactamente un commit al final del batch
    mock_session.commit.assert_called_once()
