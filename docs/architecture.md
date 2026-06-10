# Arquitectura de uy-precios

> Documentación técnica del sistema de scraping y comparación de precios de supermercados uruguayos.

---

## Tabla de contenidos

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Stack tecnológico](#3-stack-tecnológico)
4. [Modelo de datos](#4-modelo-de-datos)
5. [Capa de scraping](#5-capa-de-scraping)
6. [Pipeline de normalización](#6-pipeline-de-normalización)
7. [Orquestador del pipeline](#7-orquestador-del-pipeline)
8. [Aplicación FastAPI y scheduling](#8-aplicación-fastapi-y-scheduling)
9. [Schemas Pydantic](#9-schemas-pydantic)
10. [Capa API REST](#10-capa-api-rest)
11. [Gestión de la base de datos](#11-gestión-de-la-base-de-datos)
12. [Configuración e infraestructura](#12-configuración-e-infraestructura)
13. [Frontend React](#13-frontend-react)
14. [Estructura del repositorio](#14-estructura-del-repositorio)
15. [Guía operacional](#15-guía-operacional)
16. [Cómo agregar un supermercado nuevo](#16-cómo-agregar-un-supermercado-nuevo)
17. [Testing](#17-testing)
18. [Limitaciones conocidas y decisiones de diseño](#18-limitaciones-conocidas-y-decisiones-de-diseño)

---

## 1. Resumen ejecutivo

**uy-precios** es un sistema backend que resuelve un problema concreto: en Uruguay no existe ninguna fuente centralizada y actualizada de precios de supermercados. Los consumidores deben visitar cada sitio manualmente para comparar.

El sistema automatiza ese proceso mediante tres etapas encadenadas:

1. **Scraping diario** — Playwright navega los sitios de Tienda Inglesa y Disco como un browser real, extrae cada producto con su precio y lo almacena.
2. **Normalización** — Un pipeline de fuzzy matching agrupa productos equivalentes entre cadenas bajo una entidad canónica única, habilitando las comparaciones.
3. **API REST** — FastAPI expone los datos estructurados con documentación Swagger automática para consumo del frontend o integraciones externas.

La arquitectura está diseñada para ser operada con un único comando (`docker compose up`) y para incorporar nuevos supermercados sin modificar el núcleo del sistema.

### Alcance de la implementación actual

| Componente | Estado |
|---|---|
| Infraestructura Docker | ✅ Completo |
| Modelo de datos + migraciones Alembic | ✅ Completo |
| Clase base de scrapers | ✅ Completo |
| Scraper Tienda Inglesa | ✅ Estructura completa (selectores CSS pendientes de verificación contra sitio real) |
| Scraper Disco | ✅ Estructura completa (selectores CSS pendientes de verificación contra sitio real) |
| Pipeline de normalización | ✅ Completo |
| Orquestador del pipeline | ✅ Completo |
| Scheduler APScheduler | ✅ Completo |
| Registro de ejecuciones (`scrape_runs`) | ✅ Completo (Fase 2) |
| Schemas Pydantic (request/response) | ✅ Completo (Fase 2) |
| Endpoints API REST `/api/v1/products` | ✅ Completo (Fase 2) |
| Endpoints API REST `/api/v1/supermarkets` | ✅ Completo (Fase 2) |
| Endpoints de sistema `/health` y `/scrapes` | ✅ Completo (Fase 2) |
| Tests de API con SQLite en memoria | ✅ Completo (Fase 2) |
| Frontend React | ✅ Completo (Fase 3) |

---

## 2. Arquitectura del sistema

### Diagrama de componentes

```
┌──────────────────────────────────────────────────────────────────┐
│                         Docker Compose                            │
│                                                                   │
│   ┌─────────────────────┐         ┌──────────────────────────┐   │
│   │      backend        │         │           db             │   │
│   │                     │         │                          │   │
│   │  ┌───────────────┐  │         │   PostgreSQL 16          │   │
│   │  │  APScheduler  │  │         │                          │   │
│   │  │  (cron 9 UTC) │  │         │   • supermarkets         │   │
│   │  └──────┬────────┘  │         │   • products             │   │
│   │         │ diario    │         │   • supermarket_products │   │
│   │  ┌──────▼────────┐  │  async  │   • price_history        │   │
│   │  │  run.py       │◀─┼─────────┤   • scrape_runs          │   │
│   │  │  (pipeline)   │──┼────────▶│                          │   │
│   │  └──────┬────────┘  │         └──────────────────────────┘   │
│   │         │           │                        ▲                │
│   │  ┌──────▼────────┐  │                        │                │
│   │  │   Scrapers    │  │                        │                │
│   │  │  Playwright   │  │         ┌──────────────┴───────────┐   │
│   │  └───────────────┘  │         │        FastAPI            │   │
│   │                     │         │  GET /api/v1/products     │   │
│   │  ┌───────────────┐  │         │  GET /api/v1/supermarkets │   │
│   │  │  FastAPI      │◀─┼─────────│  GET /health             │   │
│   │  │  :8000/docs   │──┼────────▶│  GET /scrapes            │   │
│   │  └───────────────┘  │         └──────────────────────────┘   │
│   └─────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────┘
```

### Flujo de datos — pipeline de scraping

```
[APScheduler 9:00 UTC]
        │
        ▼
[run_all_scrapers()]
        │
        ▼ sesión separada
[_start_scrape_run()]  → INSERT scrape_runs (status='running') → COMMIT
        │ scrape_run_id
        │
        ├──────────────────────────────────────┐
        │                                      │
        ▼                                      ▼
[TiendaInglesaScraper]              [DiscoScraper]
[    .scrape_all()    ]             [ .scrape_all()]
        │                                      │
        └──────────────┬───────────────────────┘
                       │ asyncio.gather()
                       ▼
             [list[ScrapedProduct]]
                       │
                       ▼
          [_save_scraped_products()]
          • Batch lookup por external_id
          • Upsert en supermarket_products
          • INSERT en price_history (append-only)
                       │
                       ▼ session.commit()
                       │
                       ▼
          [ProductNormalizer.normalize_all()]
          • Preprocess name_raw
          • token_sort_ratio vs canónicos
          • Asignar product_id o crear nuevo
                       │
                       ▼ session.commit()
                       │
                       ▼
              [Resumen en stdout]
                       │
                       ▼ sesión separada
[_finish_scrape_run()]  → UPDATE scrape_runs (status='completed'|'failed') → COMMIT
```

### Flujo de datos — API REST

```
[HTTP Request]
      │
      ▼
[FastAPI Router]
      │
      ├── GET /api/v1/products          → window fn + CTE paginado → ProductSummaryResponse[]
      ├── GET /api/v1/products/{id}     → ORM + window fn → ProductDetailResponse
      ├── GET /api/v1/products/{id}/prices   → window fn → CurrentPriceResponse[]
      ├── GET /api/v1/products/{id}/history  → serie temporal → PriceHistoryResponse
      ├── GET /api/v1/products/{id}/compare  → prices + cálculo → CompareResponse
      ├── GET /api/v1/supermarkets           → ORM → SupermarketResponse[]
      ├── GET /api/v1/supermarkets/{slug}/products → EXISTS + window fn → paginado
      ├── GET /health                   → SELECT 1 + last ScrapeRun → HealthResponse
      └── GET /scrapes                  → ORM paginado → ScrapeRunResponse[]
```

### Principios de diseño

| Principio | Aplicación en el sistema |
|---|---|
| **Scrape parcial válido** | Si un supermercado retorna 800 de 1000 productos, se guardan los 800 sin considerarlo un fallo |
| **Datos históricos inmutables** | `price_history` es append-only; nunca se actualiza un registro existente |
| **Idempotencia del pipeline** | El normalizador solo procesa filas con `product_id IS NULL`; ejecutarlo dos veces es seguro |
| **Fallo aislado** | Si un scraper lanza una excepción, el otro continúa (via `asyncio.gather(return_exceptions=True)`) |
| **Extensibilidad por convención** | Agregar un supermercado nuevo = un archivo nuevo que hereda `BaseScraper`; nada más cambia |
| **Auditoría de ejecuciones** | Cada run del pipeline crea un `ScrapeRun` que persiste aunque el pipeline falle, usando una sesión DB independiente |

---

## 3. Stack tecnológico

| Capa | Tecnología | Versión | Justificación |
|---|---|---|---|
| Lenguaje | Python | 3.12 | Ecosistema de scraping maduro; tipado estático mejorado |
| Web scraping | Playwright | latest | Los sitios objetivo cargan productos con JavaScript; Playwright controla un browser Chromium real y puede ejecutar JS, esperar selectores y hacer scroll |
| Scheduling | APScheduler | latest | Librería embebida en el proceso; sin overhead operacional de Airflow/Prefect para un job diario |
| Framework API | FastAPI | latest | Alto rendimiento asíncrono, tipado con Pydantic, documentación Swagger automática |
| Base de datos | PostgreSQL | 16 | Soporte robusto para queries de series temporales, tipos numéricos precisos (`NUMERIC`), y constrains complejos |
| ORM | SQLAlchemy | 2.x | API moderna con `Mapped`/`mapped_column`, soporte async nativo |
| Migraciones | Alembic | latest | Versionado de schema con upgrade/downgrade; integrado con SQLAlchemy |
| Driver async | asyncpg | latest | Driver PostgreSQL nativo async; mejor rendimiento que psycopg2 para operaciones concurrentes |
| Fuzzy matching | rapidfuzz | latest | Implementación C de algoritmos de distancia de strings; significativamente más rápido que `fuzzywuzzy` |
| Validación | Pydantic | v2 | Validación de datos y configuración de entorno con `pydantic-settings` |
| Contenedores | Docker Compose | v2 | Un comando levanta todo el stack; parity entre entornos |
| Cliente HTTP de tests | httpx | latest | `AsyncClient` + `ASGITransport` para tests de integración sin servidor real |
| Driver SQLite async | aiosqlite | latest | Backend de tests en memoria; elimina la dependencia de PostgreSQL en el CI |
| Framework UI | React | 18.3 | Biblioteca de componentes declarativa con hooks |
| Build tool | Vite | 5.4 | Dev server con HMR y proxy inverso al backend; bundler de producción |
| Lenguaje frontend | TypeScript | 5.6 | Tipado estático; los tipos del cliente espejean los schemas Pydantic del backend |
| Estilos | Tailwind CSS | 3.4 | Clases utilitarias; sin archivos CSS custom en el proyecto |
| Gráficos | Recharts | 2.13 | Wrapper React de D3; consume el endpoint `/history` directamente |
| Router frontend | React Router | 6.27 | Navegación client-side entre `/` y `/products/:id` |

---

## 4. Modelo de datos

### Decisión de diseño central

El sistema separa el **producto canónico** del **producto por supermercado**. Esta distinción habilita las comparaciones entre cadenas:

- Un **`product`** representa la identidad normalizada de un bien: *"Leche Conaprole Entera 1L"*
- Un **`supermarket_product`** representa cómo ese bien aparece en una cadena específica: *"CONAPROLE LECHE ENTERA 1 LT"* en Disco
- **`price_history`** registra el precio de cada `supermarket_product` en cada fecha de scrape

Sin esta separación sería imposible responder: *"¿Cuánto cuesta esta leche en Tienda Inglesa vs. Disco hoy?"*

### Diagrama entidad-relación

```
supermarkets                 products
─────────────                ─────────────────
id         PK                id         PK
slug       UNIQUE             name
name                          category
base_url                      brand
active                        unit
created_at                    created_at
     │                        updated_at
     │                             │
     │ 1                           │ 1
     │                             │
     ▼ N                           ▼ N
supermarket_products ──────────────┘
────────────────────
id             PK
product_id     FK → products.id        (NULL hasta normalización)
supermarket_id FK → supermarkets.id    NOT NULL
external_id    VARCHAR(255)            (ID del producto en el sitio)
name_raw       VARCHAR(255) NOT NULL
url
image_url
active
created_at
updated_at

UNIQUE(supermarket_id, external_id)
INDEX: idx_supermarket_products_product (product_id)
     │
     │ 1
     │
     ▼ N
price_history
─────────────
id                     PK
supermarket_product_id FK → supermarket_products.id    NOT NULL
price                  NUMERIC(10,2)
currency               CHAR(3) DEFAULT 'UYU'
scraped_at             TIMESTAMPTZ
date                   DATE NOT NULL

INDEX: idx_price_history_product_date (supermarket_product_id, date DESC)


scrape_runs                          ← tabla independiente (no FK a ninguna otra)
───────────
id             PK
started_at     TIMESTAMPTZ DEFAULT NOW()
finished_at    TIMESTAMPTZ NULL
status         VARCHAR(20) DEFAULT 'running'   -- 'running' | 'completed' | 'failed'
scrape_stats   JSON NULL    -- {"tienda_inglesa": {"scraped": N, ...}, "disco": {...}}
norm_stats     JSON NULL    -- {"processed": N, "matched_auto": N, ...}
error_message  TEXT NULL
```

### Descripción de tablas

#### `supermarkets`

Catálogo de supermercados registrados en el sistema. El campo `slug` (`'tienda_inglesa'`, `'disco'`) es el identificador interno que usan los scrapers para asociar sus resultados con el registro correcto. El campo `active` permite desactivar un supermercado sin eliminarlo.

#### `products`

Productos canónicos, independientes de cualquier supermercado. Un registro en esta tabla representa la identidad abstracta de un bien de consumo. Los campos `category`, `brand` y `unit` son opcionales y se completan progresivamente (en v1, el pipeline de normalización crea canónicos con solo el `name` normalizado; los metadatos adicionales son un enhancement futuro).

#### `supermarket_products`

La representación concreta de un producto tal como existe en un supermercado específico: nombre exacto del sitio, URL de la ficha, imagen, y el `external_id` del supermercado (si lo expone). El campo `product_id` comienza en `NULL` tras el scrape y es rellenado por el pipeline de normalización. La restricción `UNIQUE(supermarket_id, external_id)` garantiza que no se dupliquen productos con ID conocido.

#### `price_history`

Serie temporal de precios. Es **append-only por diseño**: nunca se modifican ni eliminan registros. Cada ejecución del scraper genera una nueva fila por producto activo. El campo `date` (tipo `DATE`) existe para facilitar queries por día sin parsear timestamps. El índice compuesto `(supermarket_product_id, date DESC)` optimiza las queries más frecuentes: *"dame los precios de este producto en los últimos 30 días"*.

#### `scrape_runs`

Registro de auditoría de cada ejecución del pipeline. Es **independiente de todas las otras tablas** (sin FK salientes): esto es intencional para que un `ScrapeRun` con `status='failed'` pueda persistir aunque la sesión principal del pipeline haya hecho rollback. Los campos `scrape_stats` y `norm_stats` almacenan los contadores de la ejecución como JSON, permitiendo queries de análisis sin parsear logs.

---

## 5. Capa de scraping

### Clase base — `app/scrapers/base.py`

Toda la infraestructura de scraping se articula alrededor de dos abstracciones:

#### `ScrapedProduct` (dataclass)

Contrato de datos que devuelve cualquier scraper. Campos:

| Campo | Tipo | Descripción |
|---|---|---|
| `external_id` | `str \| None` | ID del producto en el sitio del supermercado (si existe) |
| `name_raw` | `str` | Nombre exacto tal como aparece en el sitio, sin modificar |
| `price` | `float` | Precio numérico ya limpiado de símbolos de moneda |
| `currency` | `str` | Código de moneda (`'UYU'`) |
| `url` | `str` | URL completa de la ficha del producto |
| `image_url` | `str \| None` | URL de la imagen del producto |
| `category` | `str \| None` | Categoría de la navegación del sitio |

#### `BaseScraper` (ABC)

Clase base abstracta que define el contrato que deben implementar todos los scrapers y provee tres métodos concretos heredables:

**Métodos abstractos** (deben implementar las subclases):

```
scrape_all() → list[ScrapedProduct]
    Navega el sitio completo por las categorías configuradas.
    Devuelve todos los productos encontrados en una sola ejecución.

scrape_category(category_url: str) → list[ScrapedProduct]
    Scrapea una URL de categoría específica de forma standalone.
    Útil para debugging y testing de categorías individuales.
```

**Métodos concretos** (heredados por todos los scrapers):

```
_get_browser_context(playwright) → BrowserContext
    Lanza un browser Chromium headless y crea un contexto con:
    • User-agent rotado aleatoriamente entre 4 agentes reales (Chrome/Firefox)
    • Viewport 1280×720
    • Locale 'es-UY'
    La rotación de user-agent reduce la probabilidad de bloqueo por detección de bots.

_safe_scrape_with_retry(fn, max_retries=3) → list[ScrapedProduct]
    Wrapper de resiliencia. Recibe una función callable de cero argumentos que
    devuelve una coroutine. La ejecuta hasta 3 veces con backoff exponencial:
    • Intento 1 falla → espera 1s
    • Intento 2 falla → espera 2s
    • Intento 3 falla → retorna []
    Un scrape parcial (lista vacía) es preferible a propagar la excepción.

_random_delay() → None
    Espera entre 1 y 3 segundos (distribución uniforme) antes de la
    siguiente request. Imita comportamiento humano de navegación para
    reducir la probabilidad de bloqueo por rate limiting.
```

El campo `_USER_AGENTS` es una variable de clase (`list[str]`) sobreescribible por subclases que necesiten user-agents distintos para un sitio específico.

### Scrapers concretos

#### `TiendaInglesaScraper` — `app/scrapers/tienda_inglesa.py`

Implementación para `https://www.tinglesa.com.uy`. El sitio usa la plataforma VTEX, que es una SPA React con renderizado client-side.

**Categorías configuradas:**

| Clave | URL |
|---|---|
| `lacteos` | `/lacteos-y-huevos` |
| `carnes` | `/carnes-y-aves` |
| `verduras` | `/frutas-y-verduras` |
| `bebidas` | `/bebidas` |
| `limpieza` | `/limpieza-del-hogar` |

> **Nota operacional:** Las URLs y los selectores CSS están marcados con comentarios `TODO` que deben verificarse contra el sitio real antes del primer run en producción. Ver instrucciones en el docstring del módulo.

**Flujo de `scrape_all()`:**

1. Crea un único browser Playwright compartido para toda la sesión
2. Itera sobre `CATEGORIES`, llamando a `_safe_scrape_with_retry(lambda: _scrape_category_page(...))` por cada una
3. Ejecuta `_random_delay()` entre categorías
4. Cierra el contexto en el bloque `finally`

**Flujo de `_scrape_category_page(page, url, category_name)`:**

1. Navega a la URL (`wait_until="networkidle"`, con fallback a `"load"` si expira)
2. Espera a que aparezca el primer selector de tarjeta de producto (`wait_for_selector`)
3. Llama a `_load_all_products(page)` para expandir el listado completo
4. Llama a `_extract_products(page, category_name)` para parsear cada tarjeta

**Estrategia de carga completa (`_load_all_products`):**

Detecta automáticamente la estrategia del sitio:
- Si existe un botón "Cargar más" visible → `_click_load_more_until_exhausted()`
- Si no → `_scroll_until_stable()` (scroll infinito)

Ambas estrategias tienen un tope de seguridad de 80 iteraciones (~1.600 productos).

**Parser de tarjeta (`_parse_card`):**

Extrae nombre, precio, URL, `external_id` e imagen de cada tarjeta. Si falta el nombre o el precio no es parseable, el producto se omite con un `logger.warning`; el resto del listado continúa procesándose.

**Parser de precio (`_parse_price`):**

Convierte strings de precio uruguayo a `float`. Maneja los formatos:
- `"$1.299"` → `1299.0` (punto como separador de miles)
- `"$ 1.299,90"` → `1299.9` (formato completo con decimales)
- `"1299"` → `1299.0` (sin separadores)

#### `DiscoScraper` — `app/scrapers/disco.py`

Implementación para `https://www.disco.com.uy`. Mismo contrato que `TiendaInglesaScraper` pero con selectores CSS orientados a un stack React genérico con atributos `data-testid` (patrón más común en plataformas Cencosud).

**Diferencias respecto a Tienda Inglesa:**

- Selectores CSS distintos (ver constantes `_SEL_*` en el módulo)
- `_parse_price` con lógica adicional para distinguir `"89.90"` (punto decimal) de `"1.299"` (punto como miles), basándose en la cantidad de dígitos después del punto
- Incluye un bloque `if __name__ == "__main__"` para ejecución standalone de debugging

**Ejecución standalone:**

```bash
docker compose exec backend python -m app.scrapers.disco
```

Imprime el total de productos encontrados y una muestra de los primeros 5 con nombre, precio y categoría.

---

## 6. Pipeline de normalización

### El problema

Los supermercados usan nombres distintos para el mismo producto:

| Supermercado | Nombre en el sitio |
|---|---|
| Tienda Inglesa | `"Leche Conaprole Entera 1L"` |
| Disco | `"CONAPROLE LECHE ENTERA 1 LT"` |

Sin normalización, son dos registros distintos y no se puede comparar su precio.

### Implementación — `app/services/normalizer.py`

#### `ProductNormalizer.normalize_all(db_session)`

Método principal. **Es idempotente**: filtra solo `supermarket_products` con `product_id IS NULL`, por lo que ejecutarlo N veces produce el mismo resultado que ejecutarlo una vez.

**Parámetros:** `db_session: AsyncSession`

**Retorna:** diccionario con estadísticas de la ejecución:

```python
{
    "processed":         int,  # total de productos procesados
    "matched_auto":      int,  # asignados automáticamente (score ≥ 90)
    "matched_tentative": int,  # asignados con confianza media (score 70-89)
    "created_new":       int,  # nuevos canónicos creados (score < 70)
}
```

**Algoritmo:**

```
1. SELECT supermarket_products WHERE product_id IS NULL  → unmatched

2. SELECT products  → construir canonical_index: list[(id, preprocessed_name)]
   (preprocesado una sola vez fuera del loop por eficiencia)

3. Para cada sp en unmatched:
   a. preprocessed = _preprocess(sp.name_raw)
   b. (product_id, score) = _find_best_match(preprocessed, canonical_index)

   c. Si score ≥ 90:
      sp.product_id = product_id
      → auto-match

   d. Si 70 ≤ score < 90:
      sp.product_id = product_id
      logger.warning(...)
      → match tentativo (requiere revisión manual)

   e. Si score < 70:
      new_product = Product(name=preprocessed)
      session.add(new_product)
      await session.flush()           ← obtener ID antes de continuar
      sp.product_id = new_product.id
      canonical_index.append(...)     ← actualizar índice en memoria
      → nuevo canónico

4. await session.commit()  ← un único commit al final del batch
```

La actualización del `canonical_index` en el paso 3e es crítica: permite que los productos procesados más adelante en el mismo batch puedan matchear contra canónicos recién creados, evitando crear duplicados cuando el mismo producto aparece en ambos supermercados.

#### `ProductNormalizer._preprocess(name: str) → str`

Método de clase que normaliza un nombre de producto para comparación. Pasos en orden:

| Paso | Transformación | Ejemplo |
|---|---|---|
| 1. Minúsculas | `str.lower()` | `"LECHE ENTERA"` → `"leche entera"` |
| 2. Eliminar tildes | Normalización NFKD + filtro de combining characters | `"Lácteos"` → `"Lacteos"` |
| 3. Normalizar unidades | Regex sobre 10 patrones de unidades de medida | `"1 litro"` → `"1l"`, `"500 gr"` → `"500g"`, `"1 kilo"` → `"1kg"` |
| 4. Remover especiales | `re.sub(r"[^a-z0-9\s]", " ", text)` | `"Prod. (Light)"` → `"Prod  Light "` |
| 5. Colapsar espacios | `re.sub(r"\s+", " ", text).strip()` | `"leche  entera"` → `"leche entera"` |

Resultado para el par del spec:
- `"CONAPROLE LECHE ENTERA 1 LT"` → `"conaprole leche entera 1l"`
- `"Leche Conaprole Entera 1L"` → `"leche conaprole entera 1l"`

(Mismo conjunto de tokens → `token_sort_ratio` = 100)

#### `ProductNormalizer._find_best_match(preprocessed, canonical_index) → (product_id | None, float)`

Método estático que ejecuta el fuzzy matching usando `rapidfuzz.fuzz.token_sort_ratio`.

`token_sort_ratio` ordena los tokens de ambos strings alfabéticamente antes de calcular la similitud de caracteres, lo que lo hace robusto al orden de palabras: `"leche entera conaprole"` y `"conaprole leche entera"` producen score 100.

Internamente usa `rapidfuzz.process.extractOne`, que recorre todos los canónicos en una sola pasada optimizada en C y devuelve el mejor match junto con su score e índice.

**Casos borde:**
- Si `canonical_index` está vacío → `(None, 0.0)` (el score 0 cae en el branch "crear nuevo")
- Si `extractOne` retorna `None` (string vacío) → `(None, 0.0)`

#### `normalize_all_standalone()`

Función de módulo que crea su propia sesión de DB y ejecuta el pipeline. Útil para llamadas manuales sin acceso al contexto de FastAPI:

```bash
docker compose exec backend python -c "
import asyncio
from app.services.normalizer import normalize_all_standalone
print(asyncio.run(normalize_all_standalone()))
"
```

---

## 7. Orquestador del pipeline

### `app/scrapers/run.py`

Punto de entrada del pipeline completo. Es el único lugar del sistema que conoce a todos los scrapers y los une con la capa de persistencia.

#### `run_all_scrapers()`

Función pública que orquesta el scrape completo de extremo a extremo. Es llamada tanto por el scheduler de APScheduler como desde `__main__`.

**Fases:**

```
1. REGISTRO DE INICIO (sesión separada)
   _start_scrape_run()
   → INSERT scrape_runs (status='running')
   → COMMIT inmediato con sesión independiente
   → Retorna scrape_run_id (o None si la DB falla)

2. HEALTH CHECK
   Verifica conectividad a la DB con SELECT 1.
   Si falla → RuntimeError (fallo rápido, no intentar scraping).

3. SCRAPING PARALELO
   asyncio.gather(
       _run_scraper(TiendaInglesaScraper()),
       _run_scraper(DiscoScraper()),
       return_exceptions=True
   )
   Si un scraper lanza excepción → se captura como valor de retorno,
   se loguea, y el otro scraper no se ve afectado.

4. PERSISTENCIA
   Para cada scraper con resultados:
   → _save_scraped_products(session, products, supermarket)
   → session.commit()

5. NORMALIZACIÓN
   → ProductNormalizer().normalize_all(session)
   (incluye su propio session.commit() interno)

6. RESUMEN
   → _print_summary(save_stats, norm_stats, elapsed)

7. REGISTRO DE FIN (sesión separada)
   _finish_scrape_run(scrape_run_id, 'completed', save_stats, norm_stats)
   → UPDATE scrape_runs SET status, finished_at, scrape_stats, norm_stats
   → COMMIT

   Si ocurre cualquier excepción en fases 2-6:
   _finish_scrape_run(scrape_run_id, 'failed', error_message=str(exc))
   → La excepción se re-lanza después del UPDATE
```

**Por qué sesiones separadas para `ScrapeRun`:**

Los helpers `_start_scrape_run` y `_finish_scrape_run` abren y cierran su propia `AsyncSession`, independiente de la sesión principal del pipeline. Si la sesión principal hace rollback por un error, el registro de `ScrapeRun` con `status='failed'` persiste igualmente. Sin esta separación, un fallo en el pipeline también revertiría el registro del fallo.

Ambos helpers manejan sus propias excepciones con `try/except` y solo loguean un `WARNING` si la DB está completamente caída, sin propagar el error al caller.

#### `_save_scraped_products(session, scraped, supermarket) → {inserted, updated}`

Función de upsert que persiste los resultados de un scraper para un supermercado dado.

**Lógica de deduplicación:**

Separa los productos en dos grupos antes de procesar:

**Grupo A — Productos con `external_id`** (caso mayoritario):
1. Batch query `SELECT ... WHERE external_id IN (...)` para traer todos los existentes en una sola operación
2. Construye un dict `{external_id → SupermarketProduct}` para lookups O(1)
3. Por cada producto: si ya existe → actualiza `name_raw`, `url`, `image_url`; si no → `INSERT`

**Grupo B — Productos sin `external_id`** (caso minoritario):
- Lookup individual por `(supermarket_id, name_raw)` como fallback de deduplicación
- Loguea un `WARNING` por cada producto en este grupo (condición anómala)

En ambos grupos, **siempre** se inserta un nuevo registro en `price_history` (append-only).

Los `session.flush()` individuales se usan únicamente para nuevos `SupermarketProduct` (cuando se necesita el ID recién asignado para crear el `PriceHistory`). El `session.add_all(price_records)` al final agrupa todos los registros de precio en una sola operación.

#### `_print_summary(save_stats, norm_stats, elapsed_s)`

Imprime una tabla de resumen en stdout al final de cada ejecución:

```
====================================================
  RESUMEN DEL SCRAPE
====================================================
  Tiempo total: 4m 32s

  tienda_inglesa          847 productos  (nuevos: 0  actualizados: 847)
  disco                   612 productos  (nuevos: 3  actualizados: 609)

  Normalización (1459 productos procesados):
    Auto-match (≥90):     1398  (95.8%)
    Tentativo (70-89):      41  (2.8%)
    Nuevos canónicos:       20  (1.4%)
====================================================
```

---

## 8. Aplicación FastAPI y scheduling

### `app/main.py`

#### Lifespan y scheduler

FastAPI usa el patrón `lifespan` (context manager async) para manejar el ciclo de vida de la aplicación. En el startup:

1. Se registra el job `run_all_scrapers` en `AsyncIOScheduler` con trigger `cron` (hora configurable via `settings.scrape_schedule_hour`)
2. Se inicia el scheduler

En el shutdown: `scheduler.shutdown(wait=False)` para no bloquear el cierre.

La importación de `run_all_scrapers` se hace de forma diferida (dentro del `lifespan`) para evitar que el event loop no esté activo en el momento del import a nivel de módulo.

#### Registro de routers

Los dos routers de la API se registran con el prefijo `/api/v1`. Cada router ya define su propio sub-prefijo (`/products` y `/supermarkets`), resultando en rutas como `/api/v1/products`:

```python
app.include_router(products_router, prefix="/api/v1")
app.include_router(supermarkets_router, prefix="/api/v1")
```

#### Endpoints de sistema

**`GET /health`**

Devuelve el estado operacional del sistema. Requiere DB para funcionar correctamente pero nunca lanza HTTP 500 por errores de DB:

```json
{
  "status": "ok",
  "database": "connected",
  "last_scrape": {
    "started_at": "2024-11-15T09:00:00Z",
    "finished_at": "2024-11-15T09:04:32Z",
    "status": "completed",
    "total_products_scraped": 1459
  }
}
```

`total_products_scraped` se calcula sumando el campo `scraped` de cada entrada en `scrape_stats`. Si no hay ningún `ScrapeRun` en la base de datos, `last_scrape` es `null`. Si la DB está caída, `database` es `"error"` y `last_scrape` es `null`.

**`GET /scrapes`**

Historial paginado de ejecuciones del scraper, ordenado por `started_at DESC`. Incluye todos los campos de `ScrapeRun`.

Parámetros: `page` (default 1), `page_size` (default 10, máximo 50).

**`POST /scrapes/trigger`** *(solo desarrollo)*

Dispara el pipeline completo como `BackgroundTask` de FastAPI. No bloquea la respuesta HTTP.

---

## 9. Schemas Pydantic

Los schemas de respuesta de la API residen en `app/schemas/`. Todos los schemas que mapean directamente a un modelo ORM usan `model_config = ConfigDict(from_attributes=True)`.

### `app/schemas/common.py`

#### `PaginatedResponse[T]`

Schema genérico para cualquier respuesta paginada. El campo `pages` se calcula como `ceil(total / page_size)` via `@computed_field` de Pydantic v2:

```python
class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int  # computed: ceil(total / page_size)
```

Usado por `GET /api/v1/products` (con `T = ProductSummaryResponse`) y `GET /scrapes` (con `T = ScrapeRunResponse`).

### `app/schemas/supermarket.py`

#### `SupermarketResponse`

Representación de un supermercado en respuestas de API:

```python
class SupermarketResponse(BaseModel):
    id: int
    slug: str
    name: str
    base_url: str
```

### `app/schemas/product.py`

Los ocho schemas del recurso de productos, en orden de dependencia:

| Schema | Usado en | Descripción |
|---|---|---|
| `CurrentPriceResponse` | `/products`, `/prices`, `/compare` | Precio actual de un producto en un supermercado: slug, name, price, currency, date, url, image_url |
| `ProductSummaryResponse` | `/products`, `/compare` | Para el listado: id, name, category, brand, unit + lista de `CurrentPriceResponse` + `min_price` |
| `SupermarketProductDetailResponse` | `/products/{id}` | Un `supermarket_product` con su precio actual (puede ser `None` si no hay historial) |
| `ProductDetailResponse` | `/products/{id}` | Producto canónico completo con timestamps y lista de `SupermarketProductDetailResponse` |
| `PricePointResponse` | `/history` | Un punto en una serie temporal: date + price |
| `PriceHistoryResponse` | `/history` | Diccionario `{slug: list[PricePointResponse]}` para alimentar Recharts |
| `CompareEntryResponse` | `/compare` | Una fila de la tabla de comparación (similar a `CurrentPriceResponse` sin `image_url`) |
| `CompareResponse` | `/compare` | Respuesta completa: product + comparison + cheapest + difference + difference_pct |

### `app/schemas/system.py`

| Schema | Usado en | Descripción |
|---|---|---|
| `LastScrapeResponse` | `/health` | Resumen del último scrape: started_at, finished_at, status, total_products_scraped |
| `HealthResponse` | `/health` | status + database + last_scrape |
| `ScrapeRunResponse` | `/scrapes` | Todos los campos de `ScrapeRun`, mapeado desde ORM con `from_attributes=True` |

---

## 10. Capa API REST

### Estructura de routers

```
app/routers/
├── __init__.py          # vacío
├── products.py          # APIRouter(prefix="/products")
└── supermarkets.py      # APIRouter(prefix="/supermarkets")
```

Los routers se registran en `main.py` con `app.include_router(..., prefix="/api/v1")`, resultando en rutas finales de la forma `/api/v1/products/...`.

### Estrategia de queries SQL

Los endpoints de productos y supermercados requieren obtener el **precio más reciente** de cada `supermarket_product`. Existen dos enfoques SQL para esto:

**LATERAL JOIN** (PostgreSQL-only):
```sql
JOIN LATERAL (
    SELECT price, currency, date FROM price_history
    WHERE supermarket_product_id = sp.id
    ORDER BY date DESC, scraped_at DESC LIMIT 1
) ph ON true
```

**Window function con ROW_NUMBER** (portátil: PostgreSQL + SQLite):
```sql
JOIN (
    SELECT supermarket_product_id, price, currency, date,
           ROW_NUMBER() OVER (
               PARTITION BY supermarket_product_id
               ORDER BY date DESC, scraped_at DESC
           ) AS _rn
    FROM price_history
) ph ON ph.supermarket_product_id = sp.id AND ph._rn = 1
```

El sistema usa la segunda variante porque permite ejecutar los tests contra SQLite en memoria sin necesitar PostgreSQL. Para datasets reales (orden de millones de filas en `price_history`), ambas variantes son equivalentes desde el punto de vista del query planner de PostgreSQL dado que el índice `idx_price_history_product_date` es usado en ambos casos.

De forma similar, los filtros de texto usan `lower(p.name) LIKE lower(:q_pattern)` en lugar de `ILIKE` por la misma razón de portabilidad.

### Router de productos — `app/routers/products.py`

#### `GET /api/v1/products`

**Parámetros:** `q`, `category`, `supermarket`, `page`, `page_size` (máx. 100)

**Respuesta:** `PaginatedResponse[ProductSummaryResponse]`

El query usa un CTE con dos partes:
1. `paginated_ids`: selecciona `DISTINCT p.id` con los filtros, paginado
2. Query principal: re-join completo sobre los IDs del CTE para obtener todos los campos

Esto resuelve el problema de paginación con joins (sin el CTE, un `LIMIT` sobre filas denormalizadas produciría resultados incorrectos).

El campo `current_prices` de cada `ProductSummaryResponse` incluye el precio de **todos** los supermercados donde el producto tiene historial. El campo `min_price` se calcula en Python iterando sobre `current_prices`.

#### `GET /api/v1/products/{id}`

**Respuesta:** `ProductDetailResponse` | 404

Fetch del `Product` via ORM + query SQL separado para los `supermarket_products` con su último precio (usando `LEFT JOIN` con window function, para incluir también los que no tienen historial con precio `null`).

#### `GET /api/v1/products/{id}/prices`

**Respuesta:** `list[CurrentPriceResponse]` | 404

Similar al listado de precios de `/products`, pero filtrado a un producto específico y ordenado por `price ASC`. Solo incluye supermercados con al menos un registro en `price_history` (usa `JOIN`, no `LEFT JOIN`).

#### `GET /api/v1/products/{id}/history`

**Parámetros:** `from` (alias de `from_date`), `to` (alias de `to_date`)

**Respuesta:** `PriceHistoryResponse` | 404

Los parámetros `from` y `to` son keywords de Python, por lo que se declaran con `Query(alias="from")` y `Query(alias="to")` para mantener la URL limpia.

El resultado es un diccionario `{slug: [PricePointResponse]}` ya ordenado por `date ASC`, listo para consumir directamente por Recharts sin procesamiento adicional en el frontend.

#### `GET /api/v1/products/{id}/compare`

**Respuesta:** `CompareResponse` | 404

Reutiliza el helper interno `_fetch_current_prices()` que ya devuelve los precios ordenados por `price ASC`. Con ese orden garantizado:
- `cheapest` = `current_prices[0].supermarket_slug`
- `min_price` = `current_prices[0].price`
- `max_price` = `current_prices[-1].price`
- `difference` = `round(max_price - min_price, 2)`
- `difference_pct` = `round(float(difference / min_price) * 100, 2)`

Si solo hay un supermercado: `difference=0`, `difference_pct=0.0`. Si no hay ninguno: todos `None`.

**Helpers privados:**

Los helpers `_get_product_or_404` y `_fetch_current_prices` no están decorados con `@router.get`, por lo que FastAPI no los registra como endpoints pero están disponibles para cualquier handler del módulo.

### Router de supermercados — `app/routers/supermarkets.py`

#### `GET /api/v1/supermarkets`

**Respuesta:** `list[SupermarketResponse]`

Query ORM simple: `SELECT ... WHERE active = true ORDER BY name ASC`. Usa `model_validate()` para construir los schemas desde los objetos ORM.

#### `GET /api/v1/supermarkets/{slug}/products`

**Parámetros:** `page`, `page_size` (máx. 100)

**Respuesta:** `SupermarketProductsResponse` | 404

`SupermarketProductsResponse` es un schema inline definido en el router (no en `app/schemas/`) porque combina el campo `supermarket: SupermarketResponse` con los campos de paginación, lo cual no encaja en el `PaginatedResponse[T]` genérico.

A diferencia de `GET /products`, aquí `current_prices` de cada `ProductSummaryResponse` contiene **exactamente una entrada**: el precio de este supermercado. La intención es comparar precios entre cadenas en la vista de producto (`/compare`), no en el listado de cadena.

El check de "tiene al menos un precio" usa `WHERE EXISTS (SELECT 1 FROM price_history WHERE supermarket_product_id = sp.id)` en lugar de un JOIN, lo cual es semánticamente más claro para el caso de existencia.

---

## 11. Gestión de la base de datos

### Configuración — `app/db/session.py`

Crea el engine async de SQLAlchemy y el `async_sessionmaker`:

```python
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
```

`expire_on_commit=False` es importante para acceder a atributos de objetos ORM después de un commit sin necesitar un nuevo SELECT.

La función `get_db()` es un generador async diseñado para usarse como dependency injection de FastAPI:

```python
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

### Modelos SQLAlchemy — `app/models/`

Todos los modelos usan la API declarativa de SQLAlchemy 2.x con anotaciones de tipo modernas:

```python
class SupermarketProduct(Base):
    __tablename__ = "supermarket_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    name_raw: Mapped[str] = mapped_column(String(255))
    ...
```

`Mapped[T]` implica `NOT NULL`; `Mapped[T | None]` implica `nullable`. Las relationships están definidas bidireccionales usando `back_populates`.

**Archivos:**

| Archivo | Modelo | Tabla |
|---|---|---|
| `models/base.py` | `Base` | — |
| `models/supermarket.py` | `Supermarket` | `supermarkets` |
| `models/product.py` | `Product` | `products` |
| `models/supermarket_product.py` | `SupermarketProduct` | `supermarket_products` |
| `models/price_history.py` | `PriceHistory` | `price_history` |
| `models/scrape_run.py` | `ScrapeRun` | `scrape_runs` |

#### `ScrapeRun` — `app/models/scrape_run.py`

```python
class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="'running'")
    scrape_stats: Mapped[dict | None] = mapped_column(JSON)
    norm_stats: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
```

No tiene relaciones con otras tablas por diseño (ver sección 4).

### Migraciones — Alembic

**Configuración (`alembic.ini`):**

- `script_location = alembic` (directorio `backend/alembic/`)
- `prepend_sys_path = .` permite que `env.py` importe `app.models.*`
- La URL de conexión se sobreescribe en `env.py` desde la variable de entorno `DATABASE_URL`

**`alembic/env.py`** usa el patrón async de Alembic:

```python
async def run_async_migrations() -> None:
    connectable = async_engine_from_config(...)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
```

El `target_metadata = Base.metadata` importado de todos los modelos habilita la autogeneración de migraciones con `alembic revision --autogenerate`.

**Migración inicial (`a1b2c3d4e5f0_initial_schema.py`):**

Crea las 4 tablas en orden de dependencias de FK:
`supermarkets` → `products` → `supermarket_products` → `price_history`

**Migración Fase 2 (`b3c4d5e6f701_add_scrape_runs.py`):**

Agrega la tabla `scrape_runs`. Al no tener dependencias de FK con otras tablas, no requiere un orden específico de ejecución:

```python
down_revision = "a1b2c3d4e5f0"

def upgrade() -> None:
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="'running'", nullable=False),
        sa.Column("scrape_stats", sa.JSON(), nullable=True),
        sa.Column("norm_stats", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

def downgrade() -> None:
    op.drop_table("scrape_runs")
```

**Comandos operacionales:**

```bash
# Aplicar todas las migraciones pendientes
docker compose exec backend alembic upgrade head

# Crear nueva migración (después de modificar modelos)
docker compose exec backend alembic revision --autogenerate -m "descripcion"

# Revertir última migración
docker compose exec backend alembic downgrade -1

# Estado actual
docker compose exec backend alembic current
```

### Seed — `app/db/seed.py`

Inserta los dos supermercados iniciales en la tabla `supermarkets`. Es idempotente: verifica la existencia por `slug` antes de insertar.

```bash
docker compose exec backend python -m app.db.seed
```

---

## 12. Configuración e infraestructura

### Variables de entorno

Definidas en `.env.example` en la raíz del proyecto:

| Variable | Requerida | Default | Descripción |
|---|---|---|---|
| `DB_PASSWORD` | Sí | — | Contraseña de PostgreSQL |
| `DATABASE_URL` | Sí (backend) | — | URL de conexión async: `postgresql+asyncpg://uy_precios:${DB_PASSWORD}@db:5432/uy_precios` |
| `SCRAPE_SCHEDULE_HOUR` | No | `9` | Hora UTC del scrape diario |
| `LOG_LEVEL` | No | `INFO` | Nivel de logging del backend |

### Configuración del backend — `app/core/config.py`

Usa `pydantic-settings` (`BaseSettings`). Lee variables de entorno y opcionalmente de un archivo `.env`:

```python
class Settings(BaseSettings):
    database_url: str
    scrape_schedule_hour: int = 9
    log_level: str = "INFO"

    model_config = {"env_file": ".env"}
```

La instancia `settings` se crea a nivel de módulo y es importada donde se necesita.

### `docker-compose.yml`

Define tres servicios:

**`db`** — PostgreSQL 16 con volumen persistente `postgres_data`.

**`backend`** — Build desde `./backend/Dockerfile`.
- `depends_on: db`
- Puerto `8000:8000`
- Volumen `./backend:/app` para hot-reload en desarrollo
- Variables de entorno: `DATABASE_URL` (con driver `asyncpg`), `SCRAPE_SCHEDULE_HOUR`, `LOG_LEVEL`

**`frontend`** — Build desde `./frontend/Dockerfile` (Node 20 Alpine).
- `depends_on: backend`
- Puerto `5173:5173`
- Volúmenes: `./frontend:/app` (hot-reload) y `/app/node_modules` (preserva módulos del contenedor)
- Corre `npm run dev -- --host 0.0.0.0` (servidor Vite accesible desde el host)
- Proxy inverso en `vite.config.ts`: `/api/*`, `/health` y `/scrapes` → `http://backend:8000`

### `backend/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

`playwright install --with-deps chromium` instala Chromium y sus dependencias del sistema operativo (librerías gráficas necesarias para ejecutar un browser headless en Linux).

---

## 13. Frontend React

### Stack tecnológico del frontend

| Tecnología | Versión | Rol |
|---|---|---|
| React | 18.3 | Biblioteca de componentes declarativa con hooks |
| TypeScript | 5.6 | Tipado estático; los tipos del cliente espejean los schemas Pydantic del backend |
| Vite | 5.4 | Dev server con HMR y proxy inverso; bundler de producción |
| React Router | 6.27 | Navegación client-side (SPA con dos rutas) |
| Tailwind CSS | 3.4 | Clases utilitarias; sin archivos CSS custom en el proyecto |
| Recharts | 2.13 | Wrapper React de D3; consume el endpoint `/history` directamente |

### Arquitectura del frontend

El frontend es una SPA (Single Page Application) servida por el servidor de desarrollo Vite en el puerto 5173. Las llamadas HTTP se hacen a URLs relativas (e.g., `/api/v1/products`); el proxy configurado en `vite.config.ts` las redirige transparentemente al backend:

```
Frontend (Vite SPA :5173)
    │
    ├── /api/*    ──proxy──▶  http://backend:8000/api/*
    ├── /health   ──proxy──▶  http://backend:8000/health
    └── /scrapes  ──proxy──▶  http://backend:8000/scrapes
```

Este diseño evita problemas de CORS y elimina la necesidad de hardcodear la URL del backend en el código de la aplicación. `BASE_URL = ''` en el cliente HTTP hace que todas las rutas sean relativas al origen del servidor Vite.

### Páginas y rutas

El router (`App.tsx`) define dos rutas envueltas en el componente `Layout`:

| Ruta | Componente | Descripción |
|---|---|---|
| `/` | `HomePage` | Búsqueda, filtros y grid paginado de productos |
| `/products/:id` | `ProductDetailPage` | Detalle, comparación entre supermercados e historial de precios |

#### `HomePage`

Gestiona cuatro estados reactivos: `q` (texto de búsqueda), `category`, `supermarket` y `page`. Cualquier cambio en un filtro dispara un `useEffect` que llama a `GET /api/v1/products`.

**Componentes:**
- `SearchBar` con debounce de 500ms
- Sidebar colapsable en mobile (con `<details>/<summary>`) con `<select>` de categoría y supermercado
- Grid de dos columnas con `ProductCard` en desktop, una columna en mobile
- Paginación con botones "Anterior/Siguiente" cuando `pages > 1`

**Detalles de implementación:**

La bandera `cancelled = true` en el cleanup del `useEffect` evita race conditions cuando el usuario escribe rápido y llegan respuestas fuera de orden. Las categorías del `<select>` se extraen dinámicamente de los resultados actuales con `useMemo`, y si la categoría seleccionada desaparece de los resultados (por cambio de búsqueda), se resetea automáticamente. Al cambiar cualquier filtro —no la página— se resetea `page` a 1.

#### `ProductDetailPage`

Carga en paralelo con `Promise.all` los datos de comparación y el detalle completo:

```typescript
Promise.all([
  getProductCompare(id),  // precios actuales + cheapest + difference
  getProduct(id),         // detalle con name_raw por supermarket_product
])
```

Tres secciones en la página:
1. **Comparación de precios** — `PriceCompareTable` con badge "Más barato" y diferencia porcentual
2. **Historial de precios** — `PriceHistoryChart` con gráfico de líneas Recharts
3. **Nombres originales** — tabla que muestra `name_raw` de cada `supermarket_product`

Diferencia explícita entre HTTP 404 (producto no existe) y error de red/servidor: cada caso muestra un mensaje específico.

### Componentes clave

#### `PriceCompareTable`

Recibe el `CompareResponse` del endpoint `/compare`. Antes de renderizar, deduplica entradas por `supermarket_slug` con la función `deduplicateComparison()`. El primer elemento de `comparison` es el más barato (el backend ordena `ASC` por precio); la diferencia de cada fila se calcula en el cliente como `entry.price - minPrice`.

Cuando hay más de un supermercado, muestra la columna "Diferencia" con:
- Badge verde "✓ Más barato" para el supermercado con menor precio
- Texto rojo `+$N (+X.X%)` para los más caros

#### `PriceHistoryChart`

Hace su propio fetch a `GET /api/v1/products/{id}/history` (sin parámetros de fecha → sin límite por defecto). El resultado `{slug: PricePoint[]}` alimenta a Recharts con una `Line` por supermercado. Los colores están fijos en `getSupermarketColor`:

| Supermercado | Color |
|---|---|
| `tienda_inglesa` | `#2563eb` (azul) |
| `disco` | `#dc2626` (rojo) |
| Futuros supermercados | Paleta de 4 colores fallback |

#### `SearchBar`

Implementa debounce de 500ms internamente con `setTimeout`/`clearTimeout`, sin dependencias externas. El estado del texto vive en el componente padre (`HomePage`); `SearchBar` expone `value` y `onChange`.

### Cliente de API — `src/api/client.ts`

Centraliza todas las llamadas HTTP y define los tipos TypeScript que espejean los schemas Pydantic del backend:

| Función | Endpoint | Descripción |
|---|---|---|
| `getProducts(params)` | `GET /api/v1/products` | Listado paginado con `q`, `category`, `supermarket`, `page` |
| `getProduct(id)` | `GET /api/v1/products/{id}` | Detalle completo con `supermarket_products` |
| `getProductCompare(id)` | `GET /api/v1/products/{id}/compare` | Comparación entre supermercados con diferencias |
| `getProductHistory(id, params)` | `GET /api/v1/products/{id}/history` | Serie temporal `{slug: PricePoint[]}` |
| `getSupermarkets()` | `GET /api/v1/supermarkets` | Lista de supermercados activos |
| `getHealth()` | `GET /health` | Estado del sistema y último scrape |

La función interna `fetchJSON<T>` lanza un `Error` si la respuesta no es `ok`, preservando el código HTTP en el mensaje (`"Error 404: ..."`) para que los componentes puedan diferenciar 404 de otros errores.

### Utilidades — `src/utils/formatters.ts`

| Función | Entrada | Salida |
|---|---|---|
| `formatPrice(price)` | `1299` | `"$1.299"` (locale `es-UY`, separadores correctos para Uruguay) |
| `formatDate(dateStr)` | `"2024-11-15"` | `"15 de noviembre de 2024"` |
| `formatDateShort(dateStr)` | `"2024-11-15"` | `"15 nov"` |
| `getSupermarketColor(slug, idx)` | `"tienda_inglesa"` | `"#2563eb"` |

`formatPrice` usa `Intl.NumberFormat` con locale `'es-UY'`. Las funciones de fecha agregan `T00:00:00` al string antes de construir el `Date` para evitar el desplazamiento de zona horaria que ocurre cuando se parsea solo `YYYY-MM-DD`.

---

## 14. Estructura del repositorio

```
uy-precios/
│
├── docker-compose.yml          # Orquestación de contenedores
├── .env.example                # Plantilla de variables de entorno
├── spec.md                     # Especificación funcional y técnica del proyecto
│
├── docs/
│   └── architecture.md         # Este documento
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini              # asyncio_mode = auto para pytest-asyncio
│   ├── alembic.ini             # Configuración de Alembic
│   │
│   ├── alembic/
│   │   ├── env.py              # Entorno async de Alembic
│   │   ├── script.py.mako      # Template para nuevas migraciones
│   │   └── versions/
│   │       ├── a1b2c3d4e5f0_initial_schema.py    # Fase 1: tablas base
│   │       └── b3c4d5e6f701_add_scrape_runs.py   # Fase 2: tabla scrape_runs
│   │
│   ├── app/
│   │   ├── main.py             # FastAPI app + scheduler + endpoints de sistema
│   │   │
│   │   ├── core/
│   │   │   └── config.py       # Configuración via pydantic-settings
│   │   │
│   │   ├── db/
│   │   │   ├── session.py      # Engine async + AsyncSessionLocal + get_db()
│   │   │   └── seed.py         # Inserción inicial de supermercados
│   │   │
│   │   ├── models/
│   │   │   ├── base.py                    # DeclarativeBase
│   │   │   ├── supermarket.py             # Modelo Supermarket
│   │   │   ├── product.py                 # Modelo Product (canónico)
│   │   │   ├── supermarket_product.py     # Modelo SupermarketProduct
│   │   │   ├── price_history.py           # Modelo PriceHistory (append-only)
│   │   │   └── scrape_run.py              # Modelo ScrapeRun (auditoría)
│   │   │
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── common.py       # PaginatedResponse[T] genérico
│   │   │   ├── product.py      # 8 schemas del recurso /products
│   │   │   ├── supermarket.py  # SupermarketResponse
│   │   │   └── system.py       # HealthResponse, ScrapeRunResponse, LastScrapeResponse
│   │   │
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── products.py     # 5 endpoints: list, detail, prices, history, compare
│   │   │   └── supermarkets.py # 2 endpoints: list, products-by-supermarket
│   │   │
│   │   ├── services/
│   │   │   └── normalizer.py   # Pipeline de normalización con rapidfuzz
│   │   │
│   │   └── scrapers/
│   │       ├── base.py             # BaseScraper + ScrapedProduct
│   │       ├── tienda_inglesa.py   # Scraper Tienda Inglesa
│   │       ├── disco.py            # Scraper Disco
│   │       └── run.py              # Orquestador del pipeline completo
│   │
│   └── tests/
│       ├── conftest.py             # Fixtures SQLite + override get_db()
│       ├── test_normalizer.py      # Tests del pipeline de normalización
│       └── test_api.py             # Tests de integración de la API REST
│
└── frontend/
    ├── Dockerfile               # Node 20 Alpine + Vite dev server
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts           # Proxy inverso: /api/*, /health, /scrapes → backend:8000
    ├── tailwind.config.js
    └── src/
        ├── main.tsx             # Entry point de React
        ├── App.tsx              # Router raíz con dos rutas
        ├── api/
        │   └── client.ts        # Tipos TypeScript + funciones de fetch tipadas
        ├── components/
        │   ├── Layout.tsx       # Wrapper de página con header y contenedor
        │   ├── SearchBar.tsx    # Input con debounce de 500ms
        │   ├── ProductCard.tsx  # Tarjeta de producto en el grid de resultados
        │   ├── PriceCompareTable.tsx  # Tabla de comparación + badge "Más barato"
        │   ├── PriceHistoryChart.tsx  # Gráfico de líneas Recharts
        │   ├── LoadingSpinner.tsx     # Indicador de carga
        │   └── ErrorMessage.tsx       # Error con botón retry
        ├── pages/
        │   ├── HomePage.tsx     # Búsqueda + filtros sidebar + grid paginado
        │   └── ProductDetailPage.tsx  # Detalle + comparador + historial
        └── utils/
            └── formatters.ts    # formatPrice, formatDate, getSupermarketColor
```

---

## 15. Guía operacional

### Primer arranque

```bash
# 1. Crear el archivo de entorno
cp .env.example .env
# Editar .env: cambiar DB_PASSWORD por un valor seguro

# 2. Levantar todos los servicios
docker compose up --build

# 3. Aplicar migraciones (en otra terminal)
docker compose exec backend alembic upgrade head

# 4. Insertar supermercados iniciales
docker compose exec backend python -m app.db.seed

# 5. Ejecutar el primer scrape (dura entre 5 y 15 minutos)
docker compose exec backend python -m app.scrapers.run

# 6. Acceder a la aplicación
# Frontend:   http://localhost:5173
# Swagger UI: http://localhost:8000/docs
# Health:     http://localhost:8000/health
```

### Ejecutar el scrape manualmente

```bash
# Opción A: línea de comandos (ve output en tiempo real)
docker compose exec backend python -m app.scrapers.run

# Opción B: endpoint HTTP (proceso en background)
curl -X POST http://localhost:8000/scrapes/trigger
```

### Consumir la API

```bash
# Documentación interactiva Swagger
open http://localhost:8000/docs

# Buscar productos
curl "http://localhost:8000/api/v1/products?q=leche&page=1&page_size=5" | jq

# Detalle de un producto canónico
curl "http://localhost:8000/api/v1/products/42" | jq

# Precios actuales por supermercado
curl "http://localhost:8000/api/v1/products/42/prices" | jq

# Historial de precios para gráfico (últimos 30 días)
curl "http://localhost:8000/api/v1/products/42/history?from=2024-10-15&to=2024-11-15" | jq

# Comparación entre supermercados
curl "http://localhost:8000/api/v1/products/42/compare" | jq

# Supermercados activos
curl "http://localhost:8000/api/v1/supermarkets" | jq

# Productos de Tienda Inglesa (página 2)
curl "http://localhost:8000/api/v1/supermarkets/tienda_inglesa/products?page=2" | jq

# Historial de scrapes
curl "http://localhost:8000/scrapes?page=1&page_size=5" | jq
```

### Probar un scraper individual

```bash
# Disco standalone (con logging a stdout y muestra de primeros 5 productos)
docker compose exec backend python -m app.scrapers.disco

# Normalización standalone
docker compose exec backend python -c "
import asyncio
from app.services.normalizer import normalize_all_standalone
print(asyncio.run(normalize_all_standalone()))
"
```

### Consultar datos directamente en PostgreSQL

```bash
docker compose exec db psql -U uy_precios -d uy_precios

-- Cantidad de productos por supermercado
SELECT s.name, COUNT(*) AS productos
FROM supermarket_products sp
JOIN supermarkets s ON sp.supermarket_id = s.id
GROUP BY s.name;

-- Productos sin normalizar
SELECT COUNT(*) FROM supermarket_products WHERE product_id IS NULL;

-- Último precio de un producto en ambos supermercados
SELECT s.name, ph.price, ph.date
FROM price_history ph
JOIN supermarket_products sp ON ph.supermarket_product_id = sp.id
JOIN supermarkets s ON sp.supermarket_id = s.id
WHERE sp.product_id = 42
ORDER BY ph.date DESC;

-- Historial de ejecuciones del scraper
SELECT id, status, started_at, finished_at,
       scrape_stats->>'tienda_inglesa' AS ti_stats
FROM scrape_runs
ORDER BY started_at DESC
LIMIT 10;
```

---

## 16. Cómo agregar un supermercado nuevo

La arquitectura de scrapers está diseñada para que esta operación no requiera modificar ningún archivo existente.

**Pasos:**

**1. Registrar en la base de datos:**

```sql
INSERT INTO supermarkets (slug, name, base_url)
VALUES ('devoto', 'Devoto', 'https://www.devoto.com.uy');
```

**2. Crear el scraper:**

```python
# backend/app/scrapers/devoto.py

from .base import BaseScraper, ScrapedProduct

class DevotoScraper(BaseScraper):
    BASE_URL = "https://www.devoto.com.uy"

    CATEGORIES = {
        "lacteos": f"{BASE_URL}/lacteos",
        ...
    }

    def __init__(self) -> None:
        super().__init__(supermarket_slug="devoto")

    async def scrape_all(self) -> list[ScrapedProduct]:
        # Misma estructura que TiendaInglesaScraper o DiscoScraper
        ...

    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        ...
```

**3. Registrar en el orquestador** (`app/scrapers/run.py`):

```python
scrape_results = await asyncio.gather(
    _run_scraper(TiendaInglesaScraper()),
    _run_scraper(DiscoScraper()),
    _run_scraper(DevotoScraper()),   # ← agregar esta línea
    return_exceptions=True,
)

slugs = ["tienda_inglesa", "disco", "devoto"]  # ← agregar el slug
```

**4. Verificar:**

```bash
docker compose exec backend python -m app.scrapers.devoto
```

La normalización, la API y el frontend funcionan automáticamente con el nuevo supermercado: el pipeline de normalización ya agrupa canónicos multi-supermercado, y los endpoints de comparación devuelven todos los supermercados disponibles para cada producto.

---

## 17. Testing

### Estructura

```
backend/tests/
├── conftest.py          # Fixtures compartidas: SQLite engine, sesión, cliente HTTP
├── test_normalizer.py   # Tests del pipeline de normalización (Fase 1)
└── test_api.py          # Tests de integración de la API REST (Fase 2)
```

`pytest.ini` en la raíz de `backend/` configura `asyncio_mode = auto`, lo que permite escribir tests async sin decorar cada función con `@pytest.mark.asyncio`.

### Infraestructura de tests — `conftest.py`

Los tests de API usan tres fixtures encadenadas:

```
db_engine  →  db_session  →  (datos de test)
     └──────────────────────→  client
```

**`db_engine`** — Crea un engine SQLite en memoria y construye el schema completo desde `Base.metadata`. Al finalizar el test, elimina todas las tablas y cierra el engine.

**`db_session`** — Abre una `AsyncSession` conectada al engine de test. Cada test recibe una sesión limpia (la fixture `db_engine` recrea el schema para cada test).

**`client`** — Crea un `httpx.AsyncClient` con `ASGITransport(app=app)` y sobreescribe la dependency `get_db()` con una función que devuelve sesiones del engine de test. Limpia `app.dependency_overrides` al finalizar.

```python
async def override_get_db():
    async with factory() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db
```

### `test_normalizer.py`

Tests unitarios y de integración del pipeline de normalización.

**Tests síncronos (no requieren DB):**

| Test | Cobertura |
|---|---|
| `test_uppercase_a_minusculas_con_unidad_lt` | Preprocesamiento del caso central del spec |
| `test_mixed_case_con_unidad_ya_normalizada` | El otro lado del mismo par |
| `test_unidad_gramos_a_g` | Normalización de unidades (`500 gramos` → `500g`) |
| `test_unidad_ml_con_espacio` | Normalización con espaciado (`900 ML` → `900ml`) |
| `test_tildes_eliminadas` | Remoción de diacríticos |
| `test_caracteres_especiales_removidos` | Limpieza de símbolos |
| `test_espacios_multiples_colapsados` | Colapso de espacios |
| `test_leche_conaprole_score_alto` | Score ≥ 90 entre variantes del mismo producto |
| `test_aceite_girasol_score_alto` | Score ≥ 70 con "de" adicional en uno de los strings |
| `test_coca_cola_vs_pepsi_score_bajo` | Score < 70 para productos distintos de la misma categoría |
| `test_indice_vacio_devuelve_none_y_cero` | Manejo correcto del índice vacío |
| `test_elige_el_mas_parecido_entre_varios_candidatos` | Selección del mejor entre N candidatos |

**Test async (con mock de DB):**

| Test | Cobertura |
|---|---|
| `test_nuevo_canonico_creado_cuando_no_hay_match` | Flujo completo del pipeline con `AsyncMock`: producto sin match crea canónico, lo asigna, y hace exactamente un `commit` |

### `test_api.py`

Tests de integración de la API REST. Cada test inserta sus propios datos directamente con `AsyncSession` (sin mocks de ORM), lo que verifica que los queries SQL reales funcionan correctamente.

| Test | Endpoint | Cobertura |
|---|---|---|
| `test_health_sin_scrapes` | `GET /health` | Devuelve `status='ok'` y `last_scrape=null` con tabla `scrape_runs` vacía |
| `test_health_con_scrape` | `GET /health` | Refleja el último `ScrapeRun` y calcula `total_products_scraped` correctamente |
| `test_products_lista_vacia` | `GET /api/v1/products` | Devuelve `items=[]` y `total=0` sin productos en DB |
| `test_products_busqueda_por_nombre` | `GET /api/v1/products?q=leche` | Filtra correctamente con dos productos en DB (uno con "leche", uno sin) |
| `test_product_not_found` | `GET /api/v1/products/99999` | Devuelve HTTP 404 |
| `test_compare_un_solo_supermercado` | `GET /api/v1/products/{id}/compare` | Con un solo supermercado: `difference=0`, `difference_pct=0.0` |
| `test_supermarkets_lista` | `GET /api/v1/supermarkets` | Devuelve solo supermercados con `active=True` |

### Ejecutar los tests

```bash
cd backend

# Todos los tests
pytest tests/ -v

# Solo tests de API
pytest tests/test_api.py -v

# Solo tests del normalizador
pytest tests/test_normalizer.py -v

# Con cobertura
pytest tests/ --cov=app --cov-report=term-missing
```

---

## 18. Limitaciones conocidas y decisiones de diseño

### Tabla de decisiones

| Limitación | Decisión tomada | Justificación |
|---|---|---|
| Los sitios pueden cambiar su HTML | Los selectores CSS se actualizan manualmente cuando ocurre | La detección automática de cambios requiere un sistema de tests de UI end-to-end que supera el alcance del proyecto |
| Fuzzy matching imperfecto | Umbral configurable + log de matches tentativos para revisión | La alternativa (embeddings semánticos con un modelo de lenguaje) agrega complejidad operacional significativa sin cambiar el valor del portafolio |
| Sin autenticación en la API | API pública de solo lectura | No hay datos sensibles; agregar auth añadiría fricción para el uso legítimo |
| APScheduler embebido vs. Prefect/Airflow | APScheduler embebido | Airflow requiere su propia DB, workers y servidor web. Para un job diario es over-engineering. Si el sistema escala a N supermercados con jobs interdependientes, Prefect sería la migración correcta |
| Scrape parcial aceptado | Los productos disponibles se guardan aunque la sesión no complete | Un dato parcial es mejor que ningún dato; el sistema de historial permite detectar productos que dejaron de aparecer |
| `external_id` puede ser `NULL` | Fallback de deduplicación por `name_raw` | No todos los sitios exponen un ID de producto en el DOM; la garantía de deduplicación es más débil pero funcional |
| `category`, `brand`, `unit` en `products` son `NULL` en v1 | Se crean canónicos solo con `name`; los campos se enriquecen en fases posteriores | El NLP para inferir marca y unidad desde el nombre es un enhancement, no un bloqueante para la comparación de precios |
| Tests de API requieren SQL portátil | Window functions + `lower() LIKE lower()` en lugar de `LATERAL JOIN` + `ILIKE` | Permite usar SQLite en memoria como DB de tests sin necesitar PostgreSQL; ambas variantes son semánticamente equivalentes y el query planner de PostgreSQL usa los mismos índices |
| `ScrapeRun` sin FK a otras tablas | Tabla de auditoría totalmente independiente | Si la sesión principal del pipeline falla con rollback, el registro de `status='failed'` debe persistir igualmente; las FK al resto del schema crearían acoplamiento que rompería este invariante |
| `GET /supermarkets/{slug}/products` muestra solo el precio de esa cadena | Un `CurrentPriceResponse` por producto (no todos los supermercados) | El endpoint responde la pregunta "¿cuánto cuestan los productos en esta cadena?", no "¿dónde está más barato este producto?". Esa segunda pregunta la responde `/compare` |

### Nota sobre los selectores CSS de los scrapers

Los scrapers de Tienda Inglesa y Disco contienen selectores CSS marcados con `# TODO`. Estos son suposiciones razonadas (basadas en las plataformas VTEX y React respectivamente) pero **requieren verificación contra los sitios reales** antes del primer run en producción.

**Proceso de verificación:**
1. Abrir el sitio en Chrome
2. Navegar a cualquier categoría de productos
3. Inspeccionar una tarjeta de producto con DevTools
4. Mapear el elemento a la constante `_SEL_*` correspondiente en el módulo
5. Reemplazar el selector y eliminar el comentario `TODO`

Este proceso toma aproximadamente 15-20 minutos por supermercado.
