import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.scrape_run import ScrapeRun
from app.routers.products import router as products_router
from app.routers.supermarkets import router as supermarkets_router
from app.schemas.common import PaginatedResponse
from app.schemas.system import HealthResponse, LastScrapeResponse, ScrapeRunResponse

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scrapers.run import run_all_scrapers

    scheduler.add_job(
        run_all_scrapers,
        trigger="cron",
        hour=settings.scrape_schedule_hour,
        minute=0,
        id="daily_scrape",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler iniciado — scrape diario a las %02d:00 UTC", settings.scrape_schedule_hour
    )
    yield
    scheduler.shutdown(wait=False)
    logger.info("Scheduler detenido")


app = FastAPI(title="uy-precios", version="0.1.0", lifespan=lifespan)

app.include_router(products_router, prefix="/api/v1")
app.include_router(supermarkets_router, prefix="/api/v1")


@app.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    last_scrape: LastScrapeResponse | None = None
    if db_status == "connected":
        try:
            run = (
                await db.execute(
                    select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(1)
                )
            ).scalar_one_or_none()

            if run is not None:
                total: int | None = None
                if run.scrape_stats:
                    total = sum(
                        s.get("scraped", 0)
                        for s in run.scrape_stats.values()
                        if isinstance(s, dict)
                    )
                last_scrape = LastScrapeResponse(
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    status=run.status,
                    total_products_scraped=total,
                )
        except Exception:
            pass  # Si la tabla aún no existe no bloqueamos /health

    return HealthResponse(status="ok", database=db_status, last_scrape=last_scrape)


@app.get("/scrapes", response_model=PaginatedResponse[ScrapeRunResponse])
async def list_scrapes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ScrapeRunResponse]:
    total: int = (await db.execute(text("SELECT COUNT(*) FROM scrape_runs"))).scalar_one()

    offset = (page - 1) * page_size
    runs = (
        await db.execute(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(page_size).offset(offset)
        )
    ).scalars().all()

    return PaginatedResponse[ScrapeRunResponse](
        items=[ScrapeRunResponse.model_validate(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.post("/scrapes/trigger", summary="Disparar scrape manualmente (solo desarrollo)")
async def trigger_scrape(background_tasks: BackgroundTasks):
    """Encola el pipeline completo (scrape + normalización) como tarea en background.

    No bloquea la respuesta HTTP — ver los logs del servidor para el progreso.
    """
    from app.scrapers.run import run_all_scrapers

    background_tasks.add_task(run_all_scrapers)
    return {"status": "iniciado", "mensaje": "Scrape en ejecución en background — revisar logs"}
