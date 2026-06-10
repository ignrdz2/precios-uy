# Precios-uy

Comparador de precios de supermercados uruguayos. Scrapea diariamente los precios de Tienda Inglesa y Disco, los normaliza con fuzzy matching y los expone a través de una API REST y una interfaz web.

**Problema que resuelve:** No existe en Uruguay una fuente de datos centralizada y actualizada con precios de supermercados. uy-precios automatiza la comparación para que los consumidores no tengan que visitar cada sitio manualmente.

---

## Características

- **Scraping automatizado** — Playwright navega los sitios como un browser real y extrae productos con sus precios una vez por día
- **Normalización inteligente** — Pipeline de fuzzy matching agrupa productos equivalentes entre cadenas (mismo bien, distintos nombres) sin intervención manual
- **Comparación directa** — Para cada producto muestra precio, diferencia absoluta y porcentual entre supermercados
- **Historial de precios** — Gráfico de líneas con la evolución histórica de precios por supermercado
- **API REST documentada** — FastAPI con Swagger UI automático en `/docs`
- **Un solo comando** — `docker compose up` levanta todo el stack

---

## Stack tecnológico

| Capa            | Tecnología                                      |
| --------------- | ----------------------------------------------- |
| Scraping        | Python 3.12 + Playwright                        |
| Backend         | FastAPI + SQLAlchemy 2.x + PostgreSQL 16        |
| Normalización   | rapidfuzz (fuzzy matching)                      |
| Scheduling      | APScheduler (embebido en el backend)            |
| Frontend        | React 18 + TypeScript + Tailwind CSS + Recharts |
| Infraestructura | Docker Compose                                  |

---

## Requisitos previos

- [Docker](https://www.docker.com/get-started) y Docker Compose v2
- Puertos disponibles: `5432` (PostgreSQL), `8000` (backend), `5173` (frontend)

---

## Primer arranque

### 1. Clonar y configurar el entorno

```bash
git clone https://github.com/ignrdz2/precios-uy.git
cd precios-uy

cp .env.example .env
```

Editar `.env` y cambiar `DB_PASSWORD` por un valor seguro. El resto de variables tienen defaults razonables.

### 2. Levantar todos los servicios

```bash
docker compose up --build
```

Esto construye las imágenes del backend y el frontend, y levanta PostgreSQL, la API y la interfaz web.

### 3. Aplicar migraciones de base de datos

En otra terminal, mientras los servicios están corriendo:

```bash
docker compose exec backend alembic upgrade head
```

### 4. Insertar los supermercados iniciales

```bash
docker compose exec backend python -m app.db.seed
```

### 5. Ejecutar el primer scrape

```bash
docker compose exec backend python -m app.scrapers.run
```

El scraper navega ambos sitios en paralelo con Playwright. El proceso tarda entre 5 y 15 minutos dependiendo de la velocidad de respuesta de los sitios. Una vez finalizado, los datos quedan disponibles en la API y el frontend.

El scheduler corre el pipeline automáticamente cada día a las 9:00 UTC.

### 6. Acceder a la aplicación

| Servicio         | URL                          |
| ---------------- | ---------------------------- |
| **Frontend**     | http://localhost:5173        |
| **Swagger UI**   | http://localhost:8000/docs   |
| **Health check** | http://localhost:8000/health |

---

## Variables de entorno

Definidas en `.env` (copiar desde `.env.example`):

| Variable               | Default | Descripción                          |
| ---------------------- | ------- | ------------------------------------ |
| `DB_PASSWORD`          | —       | Contraseña de PostgreSQL (requerida) |
| `SCRAPE_SCHEDULE_HOUR` | `9`     | Hora UTC del scrape diario           |
| `LOG_LEVEL`            | `INFO`  | Nivel de logging del backend         |

---

## API REST

Base URL: `http://localhost:8000/api/v1`

Documentación interactiva completa en **http://localhost:8000/docs**.

### Endpoints

```
GET  /api/v1/products                        Listado paginado (filtros: q, category, supermarket)
GET  /api/v1/products/{id}                   Detalle de un producto canónico
GET  /api/v1/products/{id}/prices            Precios actuales por supermercado
GET  /api/v1/products/{id}/history           Historial para gráficos (serie temporal)
GET  /api/v1/products/{id}/compare           Comparación entre supermercados con diferencias
GET  /api/v1/supermarkets                    Lista de supermercados activos
GET  /api/v1/supermarkets/{slug}/products    Productos de un supermercado específico
GET  /health                                 Estado del sistema y último scrape
GET  /scrapes                                Historial de ejecuciones del scraper
POST /scrapes/trigger                        Dispara un scrape manual (solo desarrollo)
```

### Ejemplo — comparación de precios

```bash
curl http://localhost:8000/api/v1/products/1/compare | jq
```

```json
{
  "product": {
    "id": 1,
    "name": "Leche Conaprole Entera 1L",
    "category": "Lácteos"
  },
  "comparison": [
    {
      "supermarket_name": "Tienda Inglesa",
      "price": 89.9,
      "currency": "UYU",
      "last_updated": "2024-11-15"
    },
    {
      "supermarket_name": "Disco",
      "price": 94.5,
      "currency": "UYU",
      "last_updated": "2024-11-15"
    }
  ],
  "cheapest": "tienda_inglesa",
  "difference": 4.6,
  "difference_pct": 5.12
}
```

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────┐
│                       Docker Compose                          │
│                                                              │
│  ┌─────────────┐      ┌────────────────────┐   ┌──────────┐  │
│  │  Frontend   │      │      Backend       │   │   db     │  │
│  │  React SPA  │◀────▶│     FastAPI        │──▶│ Postgres │  │
│  │  :5173      │      │     :8000          │   │   :5432  │  │
│  └─────────────┘      └────────┬───────────┘   └──────────┘  │
│                                │                              │
│                       ┌────────▼───────────┐                 │
│                       │    APScheduler     │                 │
│                       │  (cron 9:00 UTC)   │                 │
│                       └────────┬───────────┘                 │
│                                │                              │
│                       ┌────────▼───────────┐                 │
│                       │  Playwright        │                 │
│                       │  Tienda Inglesa    │                 │
│                       │  Disco             │                 │
│                       └────────────────────┘                 │
└──────────────────────────────────────────────────────────────┘
```

**Flujo de datos:**

1. APScheduler dispara el pipeline de scraping una vez por día
2. Playwright navega Tienda Inglesa y Disco en paralelo y extrae productos con precios
3. Los datos crudos se persisten en `supermarket_products` y `price_history` (append-only)
4. El pipeline de normalización usa fuzzy matching para agrupar productos equivalentes bajo una entidad canónica en `products`
5. FastAPI expone los datos estructurados via REST
6. El frontend React consume la API y presenta búsqueda, comparación e historial

### Modelo de datos

```
supermarkets          products (canónicos)
     │                      │
     │ 1                    │ 1
     ▼ N                    ▼ N
supermarket_products ───────┘
     │ 1
     ▼ N
price_history (append-only)
```

`product_id` en `supermarket_products` comienza en `NULL` tras el scrape y es asignado por el pipeline de normalización. `price_history` nunca se modifica — cada ejecución del scraper agrega nuevas filas.

---

## Desarrollo

### Ejecutar el scraper manualmente

```bash
# Pipeline completo (ambos supermercados + normalización)
docker compose exec backend python -m app.scrapers.run

# Un scraper individual con muestra de los primeros 5 productos
docker compose exec backend python -m app.scrapers.disco

# Normalización standalone
docker compose exec backend python -c "
import asyncio
from app.services.normalizer import normalize_all_standalone
print(asyncio.run(normalize_all_standalone()))
"
```

### Tests

```bash
cd backend

# Todos los tests
pytest tests/ -v

# Solo tests de API
pytest tests/test_api.py -v

# Solo tests del normalizador
pytest tests/test_normalizer.py -v

# Con reporte de cobertura
pytest tests/ --cov=app --cov-report=term-missing
```

Los tests usan SQLite en memoria — no se requiere PostgreSQL.

### Migraciones de base de datos

```bash
# Aplicar migraciones pendientes
docker compose exec backend alembic upgrade head

# Crear migración tras modificar modelos
docker compose exec backend alembic revision --autogenerate -m "descripcion"

# Revertir última migración
docker compose exec backend alembic downgrade -1
```

---

## Agregar un supermercado nuevo

La arquitectura está diseñada para que esto no requiera modificar el núcleo del sistema:

**1. Registrar en la base de datos:**

```sql
INSERT INTO supermarkets (slug, name, base_url)
VALUES ('devoto', 'Devoto', 'https://www.devoto.com.uy');
```

**2. Crear el scraper** en `backend/app/scrapers/devoto.py`:

```python
from .base import BaseScraper, ScrapedProduct

class DevotoScraper(BaseScraper):
    BASE_URL = "https://www.devoto.com.uy"
    CATEGORIES = { "lacteos": f"{BASE_URL}/lacteos", ... }

    def __init__(self) -> None:
        super().__init__(supermarket_slug="devoto")

    async def scrape_all(self) -> list[ScrapedProduct]: ...
    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]: ...
```

**3. Registrar en el orquestador** (`backend/app/scrapers/run.py`):

```python
scrape_results = await asyncio.gather(
    _run_scraper(TiendaInglesaScraper()),
    _run_scraper(DiscoScraper()),
    _run_scraper(DevotoScraper()),  # agregar
    return_exceptions=True,
)
slugs = ["tienda_inglesa", "disco", "devoto"]  # agregar slug
```

La normalización, la API y el frontend funcionan automáticamente con el nuevo supermercado — no se requiere ningún otro cambio.

---

## Documentación técnica

Ver [docs/architecture.md](docs/architecture.md) para la documentación técnica completa: modelo de datos, algoritmo de normalización, estrategia de queries SQL, tests, decisiones de diseño y limitaciones conocidas.
