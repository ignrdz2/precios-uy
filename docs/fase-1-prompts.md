# Fase 1 — Prompts de implementación

> Pipeline completo de datos funcionando, sin frontend.
> Criterio de éxito: `python -m scrapers.run` llena la base de datos con productos reales de ambos supermercados, correctamente normalizados.

Cada prompt es autocontenido y se ejecuta en orden. Pegar uno a la vez en Claude Code.

---

## Paso 1 — Scaffolding del repositorio e infraestructura

```
Crea la estructura base del proyecto precios-uy según esta especificación.
La raíz del proyecto ya existe como la carpeta actual — no crear una carpeta contenedora nueva.

Estructura de directorios a crear dentro de la raíz:
precios-uy/  ← esta es la carpeta actual
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── models/
│       ├── routers/
│       ├── schemas/
│       ├── services/
│       └── scrapers/
└── frontend/          ← solo crear la carpeta vacía por ahora

Stack: Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2.x, Alembic, Playwright, APScheduler, rapidfuzz.

docker-compose.yml debe tener tres servicios:
- db: postgres:16, con volumen persistente, puerto 5432
- backend: build ./backend, depende de db, puerto 8000, hot reload con volumen ./backend:/app
- frontend: carpeta vacía por ahora, dejarlo comentado

.env.example:
DB_PASSWORD=changeme
SCRAPE_SCHEDULE_HOUR=9
LOG_LEVEL=INFO

DATABASE_URL en el backend debe ser: postgresql://uy_precios:${DB_PASSWORD}@db:5432/uy_precios

requirements.txt debe incluir:
fastapi, uvicorn[standard], sqlalchemy[asyncio], alembic, asyncpg, psycopg2-binary,
playwright, apscheduler, rapidfuzz, pydantic, pydantic-settings, python-dotenv

backend/Dockerfile: imagen python:3.12-slim, instala requirements, instala playwright con sus browsers, corre uvicorn con --reload.

app/main.py: solo el esqueleto de FastAPI con un GET /health que devuelve {"status": "ok"}.

No implementes lógica de negocio todavía. Solo la estructura y los archivos de configuración.
```

---

## Paso 2 — Migraciones Alembic (todas las tablas)

```
Configura Alembic y crea la migración inicial con las 4 tablas del modelo de datos.

Contexto del proyecto: backend Python 3.12 + FastAPI + SQLAlchemy 2.x async + PostgreSQL 16.
La DATABASE_URL viene de variable de entorno: postgresql+asyncpg://...
Alembic debe vivir en backend/alembic/.

Tablas a crear (en este orden por dependencias de FK):

1. supermarkets
   - id SERIAL PRIMARY KEY
   - slug VARCHAR(50) UNIQUE NOT NULL
   - name VARCHAR(100) NOT NULL
   - base_url VARCHAR(255) NOT NULL
   - active BOOLEAN DEFAULT TRUE
   - created_at TIMESTAMPTZ DEFAULT NOW()

2. products
   - id SERIAL PRIMARY KEY
   - name VARCHAR(255) NOT NULL
   - category VARCHAR(100)
   - brand VARCHAR(100)
   - unit VARCHAR(50)
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - updated_at TIMESTAMPTZ DEFAULT NOW()

3. supermarket_products
   - id SERIAL PRIMARY KEY
   - product_id INTEGER REFERENCES products(id)  ← nullable (aún no normalizado)
   - supermarket_id INTEGER REFERENCES supermarkets(id) NOT NULL
   - external_id VARCHAR(255)
   - name_raw VARCHAR(255) NOT NULL
   - url VARCHAR(500)
   - image_url VARCHAR(500)
   - active BOOLEAN DEFAULT TRUE
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - updated_at TIMESTAMPTZ DEFAULT NOW()
   - UNIQUE(supermarket_id, external_id)

4. price_history  ← append-only, nunca se modifica
   - id SERIAL PRIMARY KEY
   - supermarket_product_id INTEGER REFERENCES supermarket_products(id) NOT NULL
   - price NUMERIC(10,2) NOT NULL
   - currency CHAR(3) DEFAULT 'UYU'
   - scraped_at TIMESTAMPTZ DEFAULT NOW()
   - date DATE NOT NULL

Índices:
- idx_price_history_product_date ON price_history(supermarket_product_id, date DESC)
- idx_supermarket_products_product ON supermarket_products(product_id)

También crea un script backend/app/db/seed.py que inserte los dos supermercados iniciales:
- slug='tienda_inglesa', name='Tienda Inglesa', base_url='https://www.tinglesa.com.uy'
- slug='disco', name='Disco', base_url='https://www.disco.com.uy'

Crea los modelos SQLAlchemy correspondientes en app/models/ (uno por tabla).
Usa DeclarativeBase de SQLAlchemy 2.x con tipado moderno (Mapped, mapped_column).
```

---

## Paso 3 — BaseScraper y ScrapedProduct

```
Implementa la clase base abstracta para los scrapers en backend/app/scrapers/base.py.

Debe contener exactamente:

@dataclass
class ScrapedProduct:
    external_id: str | None
    name_raw: str
    price: float
    currency: str
    url: str
    image_url: str | None
    category: str | None

class BaseScraper(ABC):
    def __init__(self, supermarket_slug: str):
        self.supermarket_slug = supermarket_slug

    @abstractmethod
    async def scrape_all(self) -> list[ScrapedProduct]:
        ...

    @abstractmethod
    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        ...

Además, agrega en la clase base estos métodos concretos (no abstractos) para que todos los scrapers los hereden:

1. _get_browser_context(playwright): crea un context de Playwright con:
   - user_agent rotado aleatoriamente entre 3-4 user-agents reales de Chrome/Firefox
   - viewport 1280x720
   - locale 'es-UY'

2. _safe_scrape_with_retry(coroutine, max_retries=3): wrapper async que reintenta
   una coroutine hasta 3 veces con backoff exponencial (1s, 2s, 4s).
   Loguea cada fallo. Si los 3 intentos fallan, devuelve lista vacía (scrape parcial es válido).

3. _random_delay(): espera entre 1 y 3 segundos (aleatorio) para imitar comportamiento humano.

Usa logging estándar de Python (no print). El logger debe llamarse con el nombre del módulo.
```

---

## Paso 4 — Scraper de Tienda Inglesa

```
Implementa el scraper de Tienda Inglesa en backend/app/scrapers/tienda_inglesa.py.

Clase: TiendaInglesaScraper(BaseScraper), con supermarket_slug='tienda_inglesa'.

Sitio objetivo: https://www.tinglesa.com.uy
El sitio carga productos con JavaScript (requiere Playwright, no requests/httpx).

Implementa scrape_all() que:
1. Abre un browser Playwright (chromium, headless=True)
2. Navega por las categorías principales (al menos: lácteos, carnes, verduras, bebidas, limpieza)
3. Para cada categoría llama a scrape_category()
4. Acumula y devuelve todos los ScrapedProduct encontrados

Implementa scrape_category(category_url) que:
1. Navega a la URL de la categoría
2. Hace scroll hasta el final o maneja paginación si existe
3. Para cada producto en la página extrae:
   - external_id: el ID del producto en el sitio (buscar en atributos data-*, URL, o estructura del DOM)
   - name_raw: nombre exacto como aparece en el sitio (sin modificar)
   - price: precio numérico (float), remover símbolo de moneda y puntos de miles
   - currency: 'UYU'
   - url: URL completa del producto
   - image_url: URL de la imagen del producto (o None si no hay)
   - category: nombre de la categoría actual
4. Usa _random_delay() entre páginas
5. Usa _safe_scrape_with_retry() para el request de cada categoría
6. Si un producto individual falla al parsear, loguearlo y continuar (no lanzar excepción)

Requisito clave de resiliencia: si la estructura del HTML cambia y no se puede extraer el precio
de un producto, ese producto se omite (con log de warning) pero el scraper continúa.

IMPORTANTE: Si el sitio real no está disponible o su estructura es desconocida durante el
desarrollo, implementa la lógica de navegación con comentarios TODO marcando exactamente
qué selectores CSS/XPath necesitan verificarse contra el sitio real. La lógica de
extracción y transformación debe estar completa; solo los selectores pueden quedar como TODO.
```

---

## Paso 5 — Scraper de Disco

```
Implementa el scraper de Disco en backend/app/scrapers/disco.py.

Clase: DiscoScraper(BaseScraper), con supermarket_slug='disco'.

Sitio objetivo: https://www.disco.com.uy
El sitio usa React/SPA, carga productos con JavaScript (requiere Playwright).

Implementa scrape_all() y scrape_category() siguiendo exactamente el mismo contrato
que TiendaInglesaScraper (mismo BaseScraper, mismos campos de ScrapedProduct).

Categorías iniciales a cubrir: lácteos, carnes, frutas y verduras, bebidas, limpieza del hogar.

Notas específicas de Disco:
- El sitio puede tener estructura de catálogo con infinite scroll o paginación con "Ver más"
- Los precios pueden aparecer como "$ 89,90" — parsear correctamente a float 89.90
- Puede haber precio tachado (precio original) y precio con descuento; usar siempre el precio final vigente

Mismas reglas de resiliencia que el paso anterior:
- _random_delay() entre páginas
- _safe_scrape_with_retry() por categoría
- Log de warning por producto que falla, sin lanzar excepción
- TODOs para selectores que requieren verificación contra el sitio real

Al final del archivo, agrega un bloque if __name__ == '__main__': que corra el scraper
de forma standalone para facilitar el debugging:
  asyncio.run(DiscoScraper().scrape_all())
  e imprima cuántos productos encontró.
```

---

## Paso 6 — Pipeline de normalización

```
Implementa el pipeline de normalización en backend/app/services/normalizer.py.

Propósito: agrupar productos equivalentes entre supermercados bajo un producto canónico.
Ejemplo: "CONAPROLE LECHE ENTERA 1 LT" (Disco) == "Leche Conaprole Entera 1L" (Tienda Inglesa).

Usa rapidfuzz para el fuzzy matching.

Clase: ProductNormalizer

Método principal: async def normalize_all(db_session) -> dict con stats:
  {"processed": int, "matched_auto": int, "matched_tentative": int, "created_new": int}

Lógica interna:

1. PREPROCESAMIENTO (función _preprocess(name: str) -> str):
   - lowercase
   - remover caracteres especiales excepto letras, números y espacios
   - normalizar unidades:
     * "1 lt", "1lt", "1 litro" → "1l"
     * "500 gr", "500gr", "500 gramos" → "500g"
     * "1 kg", "1kg", "1 kilo" → "1kg"
   - strip y colapsar múltiples espacios

2. FUZZY MATCHING (función _find_best_match(name_raw, canonical_products) -> tuple[product_id | None, score]):
   - Usar rapidfuzz.fuzz.token_sort_ratio (robusto al orden de palabras)
   - Comparar contra todos los productos canónicos existentes
   - Devolver el mejor match y su score

3. DECISIÓN por score:
   - score >= 90 → match automático: asignar product_id al supermarket_product
   - score 70-89 → match tentativo: asignar product_id pero loguear con WARNING para revisión
   - score < 70 → nuevo producto: crear registro en tabla products con el nombre preprocesado como name

4. El pipeline debe ser idempotente: solo procesa supermarket_products donde product_id IS NULL.

5. Persistir todos los cambios en la misma db_session (commit al final del batch, no por producto).

Función standalone para testing: normalize_all_standalone() que crea su propia sesión de DB.

IMPORTANTE: el pipeline no bloquea el scraping — se llama después del scrape completo.
```

---

## Paso 7 — Punto de entrada del scraper y verificación

```
Crea el punto de entrada para correr el scrape completo manualmente.

Archivo: backend/app/scrapers/run.py (ejecutable como `python -m app.scrapers.run`)

Debe hacer:
1. Inicializar la conexión a la base de datos (leer DATABASE_URL del entorno)
2. Instanciar TiendaInglesaScraper y DiscoScraper
3. Correr ambos scrapers en paralelo con asyncio.gather()
4. Para cada producto scrapeado:
   a. Buscar si ya existe en supermarket_products por (supermarket_id, external_id)
   b. Si existe: actualizar name_raw, url, image_url; insertar nuevo registro en price_history
   c. Si no existe: insertar en supermarket_products; insertar en price_history
5. Después de guardar todos los productos, llamar a ProductNormalizer.normalize_all()
6. Imprimir resumen final:
   - Productos encontrados por supermercado
   - Productos nuevos vs actualizados
   - Stats de normalización (auto-matched, tentative, nuevos canónicos)
   - Tiempo total de ejecución

Manejo de errores:
- Si un scraper entero falla (excepción no capturada), loguearlo y continuar con el otro
- Si la DB no está disponible, fallar inmediatamente con mensaje claro

También actualiza app/main.py para:
- Incluir un endpoint POST /scrapes/trigger (solo para desarrollo) que dispara el scrape manualmente
- Integrar APScheduler que llama a run_all_scrapers() todos los días a las 9:00 UTC
- El scheduler debe iniciarse en el startup event de FastAPI y detenerse en el shutdown event

Finalmente, crea backend/tests/test_normalizer.py con al menos 5 casos de prueba para
el preprocesamiento y el matching:
- "LECHE ENTERA CONAPROLE 1 LT" vs "Leche Conaprole Entera 1l" → score alto
- "Aceite de Girasol 900ml" vs "ACEITE GIRASOL 900 ML" → score alto
- "Coca Cola 2.25L" vs "Pepsi 2L" → score bajo (no debe matchear)
- Normalización de unidades: "500 gramos" → "500g"
- Producto completamente nuevo: score < 70 crea nuevo canónico
```

---

## Notas de uso

- Los pasos 4 y 5 (scrapers específicos) requieren inspeccionar el HTML real de cada sitio.
  Correr el scraper en modo headless=False la primera vez para ver qué renderiza el sitio:
  ```python
  browser = await p.chromium.launch(headless=False)
  ```

- Para verificar que la base de datos se llenó correctamente:
  ```sql
  SELECT s.name, COUNT(sp.id) as productos, MAX(ph.scraped_at) as ultimo_scrape
  FROM supermarkets s
  JOIN supermarket_products sp ON sp.supermarket_id = s.id
  JOIN price_history ph ON ph.supermarket_product_id = sp.id
  GROUP BY s.name;
  ```

- Para ver el resultado de la normalización:
  ```sql
  SELECT p.name, COUNT(sp.id) as variantes
  FROM products p
  JOIN supermarket_products sp ON sp.product_id = p.id
  GROUP BY p.name
  HAVING COUNT(sp.id) > 1
  ORDER BY variantes DESC
  LIMIT 20;
  ```
