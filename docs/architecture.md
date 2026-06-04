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
9. [Gestión de la base de datos](#9-gestión-de-la-base-de-datos)
10. [Configuración e infraestructura](#10-configuración-e-infraestructura)
11. [Estructura del repositorio](#11-estructura-del-repositorio)
12. [Guía operacional](#12-guía-operacional)
13. [Cómo agregar un supermercado nuevo](#13-cómo-agregar-un-supermercado-nuevo)
14. [Testing](#14-testing)
15. [Limitaciones conocidas y decisiones de diseño](#15-limitaciones-conocidas-y-decisiones-de-diseño)

---

## 1. Resumen ejecutivo

**uy-precios** es un sistema backend que resuelve un problema concreto: en Uruguay no existe ninguna fuente centralizada y actualizada de precios de supermercados. Los consumidores deben visitar cada sitio manualmente para comparar.

El sistema automatiza ese proceso mediante tres etapas encadenadas:

1. **Scraping diario** — Playwright navega los sitios de Tienda Inglesa y Disco como un browser real, extrae cada producto con su precio y lo almacena.
2. **Normalización** — Un pipeline de fuzzy matching agrupa productos equivalentes entre cadenas bajo una entidad canónica única, habilitando las comparaciones.
3. **API REST** — FastAPI expone los datos estructurados con documentación Swagger automática para consumo del frontend o integraciones externas.

La arquitectura está diseñada para ser operada con un único comando (`docker compose up`) y para incorporar nuevos supermercados sin modificar el núcleo del sistema.

### Alcance de la implementación actual (Fase 1)

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
| Endpoints API REST | 🔄 Pendiente (Fase 2) |
| Frontend React | 🔄 Pendiente (Fase 3) |

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
│   │  │  run.py       │◀─┼─────────┤                          │   │
│   │  │  (pipeline)   │──┼────────▶│                          │   │
│   │  └──────┬────────┘  │         └──────────────────────────┘   │
│   │         │           │                                         │
│   │  ┌──────▼────────┐  │                                         │
│   │  │   Scrapers    │  │                                         │
│   │  │  Playwright   │  │                                         │
│   │  └───────────────┘  │                                         │
│   │                     │                                         │
│   │  ┌───────────────┐  │                                         │
│   │  │   FastAPI     │  │                                         │
│   │  │  :8000/docs   │  │                                         │
│   │  └───────────────┘  │                                         │
│   └─────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────┘
```

### Flujo de datos

```
[APScheduler 9:00 UTC]
        │
        ▼
[run_all_scrapers()]
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
```

### Principios de diseño

| Principio | Aplicación en el sistema |
|---|---|
| **Scrape parcial válido** | Si un supermercado retorna 800 de 1000 productos, se guardan los 800 sin considerarlo un fallo |
| **Datos históricos inmutables** | `price_history` es append-only; nunca se actualiza un registro existente |
| **Idempotencia del pipeline** | El normalizador solo procesa filas con `product_id IS NULL`; ejecutarlo dos veces es seguro |
| **Fallo aislado** | Si un scraper lanza una excepción, el otro continúa (via `asyncio.gather(return_exceptions=True)`) |
| **Extensibilidad por convención** | Agregar un supermercado nuevo = un archivo nuevo que hereda `BaseScraper`; nada más cambia |

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
1. HEALTH CHECK
   Verifica conectividad a la DB con SELECT 1.
   Si falla → RuntimeError (fallo rápido, no intentar scraping).

2. SCRAPING PARALELO
   asyncio.gather(
       _run_scraper(TiendaInglesaScraper()),
       _run_scraper(DiscoScraper()),
       return_exceptions=True
   )
   Si un scraper lanza excepción → se captura como valor de retorno,
   se loguea, y el otro scraper no se ve afectado.

3. PERSISTENCIA
   Para cada scraper con resultados:
   → _save_scraped_products(session, products, supermarket)
   → session.commit()

4. NORMALIZACIÓN
   → ProductNormalizer().normalize_all(session)
   (incluye su propio session.commit() interno)

5. RESUMEN
   → _print_summary(save_stats, norm_stats, elapsed)
```

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

#### Endpoints implementados

**`GET /health`**

Health check básico. Retorna:
```json
{"status": "ok"}
```

Uso: monitoreo de disponibilidad del servicio.

**`POST /scrapes/trigger`** *(solo desarrollo)*

Dispara el pipeline completo como `BackgroundTask` de FastAPI. No bloquea la respuesta HTTP; el progreso es visible en los logs del servidor.

Respuesta:
```json
{
  "status": "iniciado",
  "mensaje": "Scrape en ejecución en background — revisar logs"
}
```

#### Endpoints planificados (Fase 2)

Definidos en la spec, pendientes de implementación:

```
GET  /api/v1/products                    Lista de productos canónicos (búsqueda, filtros, paginación)
GET  /api/v1/products/{id}               Detalle de un producto canónico
GET  /api/v1/products/{id}/prices        Precio actual por supermercado
GET  /api/v1/products/{id}/history       Historial de precios (para el gráfico)
GET  /api/v1/products/{id}/compare       Comparación entre supermercados con diferencia %
GET  /api/v1/supermarkets                Lista de supermercados activos
GET  /api/v1/supermarkets/{slug}/products Todos los productos de una cadena
GET  /api/v1/scrapes                     Historial de ejecuciones del scraper
```

La documentación Swagger interactiva estará disponible en `http://localhost:8000/docs` una vez implementados.

---

## 9. Gestión de la base de datos

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

Los dos índices de performance se crean al final:
- `idx_supermarket_products_product` — para queries por `product_id` (carga del normalizador)
- `idx_price_history_product_date` — con `date DESC` para queries de historial

**Comandos operacionales:**

```bash
# Aplicar migraciones pendientes
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

## 10. Configuración e infraestructura

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

**`frontend`** — Comentado (Fase 3 pendiente).

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

## 11. Estructura del repositorio

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
│   ├── alembic.ini             # Configuración de Alembic
│   │
│   ├── alembic/
│   │   ├── env.py              # Entorno async de Alembic
│   │   ├── script.py.mako      # Template para nuevas migraciones
│   │   └── versions/
│   │       └── a1b2c3d4e5f0_initial_schema.py
│   │
│   ├── app/
│   │   ├── main.py             # FastAPI app + scheduler APScheduler
│   │   │
│   │   ├── core/
│   │   │   └── config.py       # Configuración via pydantic-settings
│   │   │
│   │   ├── db/
│   │   │   ├── session.py      # Engine async + AsyncSessionLocal
│   │   │   └── seed.py         # Inserción inicial de supermercados
│   │   │
│   │   ├── models/
│   │   │   ├── base.py             # DeclarativeBase
│   │   │   ├── supermarket.py      # Modelo Supermarket
│   │   │   ├── product.py          # Modelo Product (canónico)
│   │   │   ├── supermarket_product.py
│   │   │   └── price_history.py    # Modelo PriceHistory (append-only)
│   │   │
│   │   ├── routers/            # Endpoints FastAPI (Fase 2)
│   │   ├── schemas/            # Pydantic schemas (Fase 2)
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
│       ├── conftest.py             # Fixtures y variables de entorno de test
│       └── test_normalizer.py      # Tests del pipeline de normalización
│
└── frontend/                   # Pendiente (Fase 3)
```

---

## 12. Guía operacional

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

# 5. Verificar que el backend está operativo
curl http://localhost:8000/health
```

### Ejecutar el scrape manualmente

```bash
# Opción A: línea de comandos (ve output en tiempo real)
docker compose exec backend python -m app.scrapers.run

# Opción B: endpoint HTTP (proceso en background)
curl -X POST http://localhost:8000/scrapes/trigger
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
```

---

## 13. Cómo agregar un supermercado nuevo

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

## 14. Testing

### Estructura

Los tests residen en `backend/tests/`. `conftest.py` establece la variable `DATABASE_URL` a un valor dummy para que los módulos que importan `pydantic-settings` puedan cargarse sin una base de datos real.

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

### Ejecutar los tests

```bash
cd backend
pytest tests/ -v

# Solo tests síncronos (más rápidos)
pytest tests/ -v -k "not asyncio"

# Con cobertura
pytest tests/ --cov=app --cov-report=term-missing
```

---

## 15. Limitaciones conocidas y decisiones de diseño

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

### Nota sobre los selectores CSS de los scrapers

Los scrapers de Tienda Inglesa y Disco contienen selectores CSS marcados con `# TODO`. Estos son suposiciones razonadas (basadas en las plataformas VTEX y React respectivamente) pero **requieren verificación contra los sitios reales** antes del primer run en producción.

**Proceso de verificación:**
1. Abrir el sitio en Chrome
2. Navegar a cualquier categoría de productos
3. Inspeccionar una tarjeta de producto con DevTools
4. Mapear el elemento a la constante `_SEL_*` correspondiente en el módulo
5. Reemplazar el selector y eliminar el comentario `TODO`

Este proceso toma aproximadamente 15-20 minutos por supermercado.
