import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI

from app.core.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Importación diferida para evitar que el scheduler se importe antes
    # de que el event loop de FastAPI esté activo
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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scrapes/trigger", summary="Disparar scrape manualmente (solo desarrollo)")
async def trigger_scrape(background_tasks: BackgroundTasks):
    """Encola el pipeline completo (scrape + normalización) como tarea en background.

    No bloquea la respuesta HTTP — ver los logs del servidor para el progreso.
    """
    from app.scrapers.run import run_all_scrapers

    background_tasks.add_task(run_all_scrapers)
    return {"status": "iniciado", "mensaje": "Scrape en ejecución en background — revisar logs"}
