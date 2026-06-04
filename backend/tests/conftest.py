"""Configuración de pytest.

Establece variables de entorno mínimas para que los módulos que importan
pydantic-settings no fallen al cargarse durante los tests unitarios
(estos tests no conectan a ninguna DB real).
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_uy_precios")
