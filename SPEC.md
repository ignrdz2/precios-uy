# SPEC.md — uy-precios

> Scraper de precios de supermercados uruguayos con API pública y comparador web.

---

## 1. Visión general

**uy-precios** es un sistema que raspa diariamente los precios de productos de supermercados uruguayos, normaliza y deduplica los productos entre cadenas, expone los datos como una API REST documentada, y ofrece un frontend con comparador de precios e historial.

**Problema que resuelve:** No existe en Uruguay una fuente de datos centralizada y actualizada con precios de supermercados. Los consumidores no pueden comparar precios entre cadenas sin visitar cada sitio manualmente.

**Alcance inicial:** Tienda Inglesa y Disco. La arquitectura está diseñada para incorporar nuevos supermercados sin modificar el núcleo del sistema — solo agregando un nuevo scraper que implementa la interfaz definida.

---

## 2. Objetivos y no-objetivos

### Objetivos
- Scrapear precios diariamente de forma automatizada y resiliente
- Normalizar productos entre supermercados para habilitar comparaciones
- Exponer una API REST completa con documentación Swagger
- Ofrecer un frontend con búsqueda, comparador y gráfico de historial de precios
- Ser ejecutable localmente con un solo comando (`docker compose up`)

### No-objetivos
- Autenticación de usuarios en la API (es una API pública de lectura)
- Cobertura de todos los productos de cada supermercado en v1 (se priorizan categorías principales)
- Detección automática de cambios en la estructura HTML de los sitios (se maneja manualmente)
- Deploy en la nube (la infra está preparada para ello, pero no es parte del alcance)

---

## 3. Stack tecnológico

| Capa | Tecnología | Justificación |
|---|---|---|
| Lenguaje | Python 3.12 | Ecosistema de scraping imbatible; Playwright, rapidfuzz, FastAPI |
| Scraping | Playwright (async) | Los sitios cargan productos con JavaScript; Playwright corre un browser real |
| Scheduling | APScheduler | Librería embebida, sin overhead de Airflow/Prefect; suficiente para un job diario |
| Backend | FastAPI | Alto rendimiento, tipado, Swagger automático |
| Base de datos | PostgreSQL 16 | Queries complejas de historial y comparación; tipos de datos robustos |
| ORM | SQLAlchemy 2.x + Alembic | ORM maduro; Alembic para migraciones versionadas |
| Normalización | rapidfuzz | Fuzzy matching eficiente para agrupar productos entre supermercados |
| Frontend | React + TypeScript + Tailwind + Recharts | Consistencia con el stack ya conocido; Recharts para gráficos de historial |
| Infra | Docker Compose | Un comando levanta todo; parity con producción |

---

## 4. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Compose                       │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐  │
│  │   Scheduler  │───▶│   Scrapers   │───▶│ PostgreSQL│  │
│  │ (APScheduler)│    │  (Playwright)│    │           │  │
│  └──────────────┘    └──────────────┘    └─────┬─────┘  │
│                                                │        │
│  ┌──────────────┐                       ┌──────▼─────┐  │
│  │   Frontend   │◀──────────────────────│  FastAPI   │  │
│  │    (React)   │    REST + JSON         │            │  │
│  └──────────────┘                       └────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Flujo principal:**
1. APScheduler dispara los scrapers una vez por día (configurable)
2. Cada scraper navega el sitio del supermercado, extrae productos y precios
3. Los datos crudos se guardan en `supermarket_products` y `price_history`
4. El pipeline de normalización agrupa productos equivalentes en `products` (canónicos)
5. FastAPI expone los datos a través de endpoints REST
6. El frontend React consume la API y presenta búsqueda, comparación e historial

---

## 5. Modelo de datos

### Decisión de diseño clave

Se separa el **producto canónico** del **producto por supermercado**. Esta distinción es el corazón del sistema:

- Un `product` es la entidad normalizada: "Leche Conaprole Entera 1L"
- Un `supermarket_product` es cómo ese producto aparece en un supermercado específico: "CONAPROLE LECHE ENTERA 1 LT" en Disco
- `price_history` registra el precio de cada `supermarket_product` cada día

Sin esta separación, comparar precios entre supermercados sería imposible.

### Tablas

```sql
-- Supermercados registrados en el sistema
CREATE TABLE supermarkets (
    id          SERIAL PRIMARY KEY,
    slug        VARCHAR(50) UNIQUE NOT NULL,  -- 'tienda_inglesa', 'disco'
    name        VARCHAR(100) NOT NULL,
    base_url    VARCHAR(255) NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Producto canónico normalizado (agnóstico al supermercado)
CREATE TABLE products (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,       -- nombre normalizado
    category     VARCHAR(100),
    brand        VARCHAR(100),
    unit         VARCHAR(50),                 -- '1L', '500g', etc.
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Producto tal como existe en un supermercado específico
CREATE TABLE supermarket_products (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER REFERENCES products(id),  -- NULL si aún no normalizado
    supermarket_id  INTEGER REFERENCES supermarkets(id) NOT NULL,
    external_id     VARCHAR(255),             -- ID interno del supermercado si existe
    name_raw        VARCHAR(255) NOT NULL,    -- nombre exacto como aparece en el sitio
    url             VARCHAR(500),
    image_url       VARCHAR(500),
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(supermarket_id, external_id)
);

-- Registro histórico de precios (append-only, nunca se modifica)
CREATE TABLE price_history (
    id                      SERIAL PRIMARY KEY,
    supermarket_product_id  INTEGER REFERENCES supermarket_products(id) NOT NULL,
    price                   NUMERIC(10, 2) NOT NULL,
    currency                CHAR(3) DEFAULT 'UYU',
    scraped_at              TIMESTAMPTZ DEFAULT NOW(),
    date                    DATE NOT NULL     -- para queries por día
);

-- Índices críticos para performance
CREATE INDEX idx_price_history_product_date
    ON price_history(supermarket_product_id, date DESC);

CREATE INDEX idx_supermarket_products_product
    ON supermarket_products(product_id);
```

### Notas sobre el modelo

- `price_history` es **append-only**: nunca se actualiza un registro, solo se insertan nuevos. Esto preserva el historial completo y simplifica la lógica.
- `product_id` en `supermarket_products` puede ser `NULL` cuando el producto fue scrapeado pero aún no fue normalizado/agrupado con un canónico. El pipeline de normalización lo resuelve en un paso posterior.
- El campo `slug` en `supermarkets` es el identificador que usan los scrapers internamente (`'tienda_inglesa'`, `'disco'`).

---

## 6. Arquitectura de scrapers

### Principio de extensibilidad

Cada scraper implementa una interfaz (clase base abstracta) común. Agregar un supermercado nuevo significa crear un archivo Python nuevo que hereda de `BaseScraper` — sin tocar nada más del sistema.

```python
# scrapers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
        """Navega el sitio y devuelve todos los productos encontrados."""
        ...

    @abstractmethod
    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        """Scrapea una categoría específica."""
        ...
```

### Scrapers iniciales

```
scrapers/
├── base.py              # Clase abstracta + dataclasses
├── tienda_inglesa.py    # Implementación Tienda Inglesa
└── disco.py             # Implementación Disco
```

Para agregar Devoto en el futuro: crear `scrapers/devoto.py`, registrar el supermercado en la tabla `supermarkets`, y agregar el slug al scheduler. Nada más.

### Resiliencia del scraper

- **Reintentos con backoff:** Si una página falla, se reintenta hasta 3 veces con espera exponencial
- **Timeout por página:** Máximo 30 segundos por request antes de continuar con la siguiente
- **Logging de errores:** Cada producto que falla se loguea con la URL y el error; el scraper continúa con el resto
- **Scrape parcial es válido:** Si un supermercado devuelve 800 de 1000 productos, se guardan los 800. No es un fallo del sistema.
- **User-agent rotation:** Se rota el user-agent para reducir probabilidad de bloqueo
- **Delays aleatorios:** Entre requests, delay de 1-3 segundos aleatorio para imitar comportamiento humano

---

## 7. Pipeline de normalización

### El problema

"Leche Conaprole Entera 1L" en Tienda Inglesa puede aparecer como:
- "CONAPROLE LECHE ENTERA 1 LT" en Disco
- "Leche entera Conaprole 1 litro" en Devoto

Sin normalización, son tres productos distintos y no se puede comparar.

### Estrategia

1. **Preprocesamiento:** Lowercase, remover caracteres especiales, normalizar unidades (`1 lt` → `1l`, `500 gr` → `500g`)
2. **Fuzzy matching con rapidfuzz:** Comparar `name_raw` de productos nuevos contra los productos canónicos existentes usando `token_sort_ratio` (robusto al orden de palabras)
3. **Umbral de confianza:**
   - Score ≥ 90 → match automático, se asigna el `product_id` canónico
   - Score 70-89 → match tentativo, se loguea para revisión manual futura
   - Score < 70 → producto nuevo, se crea un nuevo `product` canónico

### Cuándo corre

El pipeline de normalización corre **después** de cada scrape completo, como un paso separado en el mismo proceso. No bloquea el scraping.

### Limitaciones (documentadas conscientemente)

El fuzzy matching no es perfecto. Un producto con packaging diferente (500ml vs 1L de la misma marca) puede matchear incorrectamente con score alto. Esta limitación se documenta en el README y es un trade-off aceptable para un portafolio — la alternativa (ML con embeddings) agrega complejidad sin cambiar el valor del proyecto.

---

## 8. API REST

Base URL: `http://localhost:8000/api/v1`

Documentación interactiva: `http://localhost:8000/docs` (Swagger UI automático de FastAPI)

### Endpoints

#### Productos

```
GET /products
  ?q=leche                    # búsqueda por nombre (fuzzy)
  ?category=lacteos           # filtro por categoría
  ?supermarket=tienda_inglesa # filtro por supermercado
  ?page=1&page_size=20        # paginación
  → Lista de productos canónicos con precio actual en cada supermercado

GET /products/{id}
  → Detalle de un producto canónico con todos sus supermarket_products

GET /products/{id}/prices
  → Precio actual en cada supermercado donde está disponible

GET /products/{id}/history
  ?from=2024-01-01&to=2024-12-31   # rango de fechas opcional
  → Historial de precios por supermercado (para el gráfico)

GET /products/{id}/compare
  → Comparación directa entre supermercados con diferencia de precio y %
```

#### Supermercados

```
GET /supermarkets
  → Lista de supermercados activos en el sistema

GET /supermarkets/{slug}/products
  → Todos los productos de un supermercado específico
```

#### Sistema

```
GET /health
  → Estado del sistema y última vez que corrió el scraper

GET /scrapes
  → Historial de scrapes (cuándo corrió, cuántos productos encontró, errores)
```

### Formato de respuesta

Todos los endpoints devuelven JSON. Ejemplo de `GET /products/{id}/compare`:

```json
{
  "product": {
    "id": 42,
    "name": "Leche Conaprole Entera 1L",
    "category": "Lácteos",
    "brand": "Conaprole",
    "unit": "1L"
  },
  "comparison": [
    {
      "supermarket": "Tienda Inglesa",
      "price": 89.90,
      "currency": "UYU",
      "last_updated": "2024-11-15",
      "url": "https://..."
    },
    {
      "supermarket": "Disco",
      "price": 94.50,
      "currency": "UYU",
      "last_updated": "2024-11-15",
      "url": "https://..."
    }
  ],
  "cheapest": "Tienda Inglesa",
  "difference": 4.60,
  "difference_pct": 5.12
}
```

---

## 9. Frontend

### Páginas

**`/` — Búsqueda principal**
- Input de búsqueda con debounce (500ms) que llama a `GET /products?q=...`
- Grid de resultados con nombre, categoría y precio más bajo encontrado
- Filtros por categoría y supermercado en sidebar

**`/products/:id` — Detalle y comparador**
- Tabla de comparación de precios entre supermercados con diferencia porcentual
- Badge visual de "más barato" en el supermercado con menor precio
- Gráfico de líneas con Recharts mostrando la evolución de precios histórica (una línea por supermercado)

### Decisiones de UX

- **Sin login:** La app es 100% pública y de solo lectura
- **Responsive:** Funciona en mobile (muchos usuarios comparan precios desde el celular)
- **Datos de fecha visible:** Siempre mostrar cuándo fue el último scrape para que el usuario sepa qué tan fresco es el dato

---

## 10. Scheduling

APScheduler está embebido en el proceso del backend (no es un servicio separado). Corre como un `BackgroundScheduler` de FastAPI al iniciar la aplicación.

```python
# scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# Corre todos los días a las 6:00 AM hora Uruguay (UTC-3)
scheduler.add_job(
    run_all_scrapers,
    trigger='cron',
    hour=9,      # 9 UTC = 6 AM UYU
    minute=0,
    id='daily_scrape',
    replace_existing=True
)
```

**¿Por qué no Airflow/Prefect?**

Airflow agrega un servidor web, una base de datos propia, workers y un scheduler separado — es over-engineering para un job diario. APScheduler embebido es suficiente y mantiene el sistema simple. Si el proyecto escala a docenas de supermercados y múltiples jobs con dependencias, migrar a Prefect sería la decisión correcta.

---

## 11. Infraestructura

### docker-compose.yml (estructura)

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: uy_precios
      POSTGRES_USER: uy_precios
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  backend:
    build: ./backend
    depends_on:
      - db
    environment:
      DATABASE_URL: postgresql://uy_precios:${DB_PASSWORD}@db:5432/uy_precios
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app  # hot reload en desarrollo

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

volumes:
  postgres_data:
```

### Variables de entorno (.env)

```
DB_PASSWORD=changeme
SCRAPE_SCHEDULE_HOUR=9       # hora UTC del scrape diario
LOG_LEVEL=INFO
```

---

## 12. Estructura del repositorio

```
uy-precios/
├── docker-compose.yml
├── .env.example
├── README.md
├── SPEC.md
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/              # migraciones de base de datos
│   │   └── versions/
│   ├── app/
│   │   ├── main.py           # FastAPI app, startup del scheduler
│   │   ├── models/           # SQLAlchemy models
│   │   │   ├── product.py
│   │   │   ├── supermarket.py
│   │   │   └── price_history.py
│   │   ├── routers/          # endpoints FastAPI
│   │   │   ├── products.py
│   │   │   └── supermarkets.py
│   │   ├── schemas/          # Pydantic schemas (request/response)
│   │   ├── services/
│   │   │   ├── normalizer.py # pipeline de normalización con rapidfuzz
│   │   │   └── scheduler.py  # configuración APScheduler
│   │   └── scrapers/
│   │       ├── base.py
│   │       ├── tienda_inglesa.py
│   │       └── disco.py
│   └── tests/
│
└── frontend/
    ├── Dockerfile
    ├── package.json
    └── src/
        ├── components/
        │   ├── SearchBar.tsx
        │   ├── ProductCard.tsx
        │   ├── PriceCompareTable.tsx
        │   └── PriceHistoryChart.tsx
        ├── pages/
        │   ├── Home.tsx
        │   └── ProductDetail.tsx
        └── api/
            └── client.ts     # funciones para consumir la API
```

---

## 13. Fases de implementación

### Fase 1 — Fundación de datos
**Objetivo:** Pipeline completo de datos funcionando, sin frontend.

1. Setup del repositorio, Docker Compose, PostgreSQL
2. Migraciones con Alembic (crear todas las tablas)
3. Implementar `BaseScraper` y `ScrapedProduct`
4. Implementar scraper de Tienda Inglesa
5. Implementar scraper de Disco
6. Implementar pipeline de normalización con rapidfuzz
7. Correr el scrape manualmente y verificar datos en PostgreSQL

**Criterio de éxito:** `python -m scrapers.run` llena la base de datos con productos reales de ambos supermercados, correctamente normalizados.

### Fase 2 — API
**Objetivo:** FastAPI completo con todos los endpoints documentados.

1. Setup FastAPI con estructura de routers
2. SQLAlchemy models + Pydantic schemas
3. Implementar todos los endpoints (productos, historial, comparación)
4. Integrar APScheduler
5. Verificar Swagger en `/docs`

**Criterio de éxito:** Todos los endpoints responden correctamente con datos reales.

### Fase 3 — Frontend
**Objetivo:** UI funcional conectada a la API.

1. Setup React + TypeScript + Tailwind
2. Página de búsqueda con debounce
3. Página de detalle con tabla de comparación
4. Gráfico de historial con Recharts
5. Responsive design

**Criterio de éxito:** Un usuario puede buscar "leche", ver los precios en ambos supermercados y el historial de precios.

---

## 14. Cómo agregar un supermercado nuevo

Esta sección existe como guía explícita de extensibilidad.

1. Insertar el supermercado en la tabla `supermarkets`:
   ```sql
   INSERT INTO supermarkets (slug, name, base_url)
   VALUES ('devoto', 'Devoto', 'https://www.devoto.com.uy');
   ```

2. Crear `backend/app/scrapers/devoto.py` que herede de `BaseScraper` e implemente `scrape_all()` y `scrape_category()`

3. Registrar el scraper en `scheduler.py` (una línea)

4. Correr el scrape manualmente para verificar

**No se requiere ningún otro cambio.** La normalización, la API y el frontend funcionan automáticamente con el nuevo supermercado.

---

## 15. Limitaciones conocidas y decisiones conscientes

| Limitación | Decisión tomada | Razón |
|---|---|---|
| Sitios pueden cambiar su HTML | Manejo manual cuando ocurre | Detección automática agrega complejidad desproporcionada |
| Fuzzy matching no es perfecto | Umbral configurable + log de matches bajos | ML con embeddings es over-engineering para este alcance |
| Sin autenticación en la API | API pública de solo lectura | No hay datos sensibles; agrega fricción innecesaria |
| APScheduler en vez de Prefect | Suficiente para un job diario | Prefect/Airflow agrega overhead operacional significativo |
| Scrape parcial aceptado | Se guardan los productos disponibles | Mejor dato parcial que falla completa |
