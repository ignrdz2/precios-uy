from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LastScrapeResponse(BaseModel):
    started_at: datetime
    finished_at: datetime | None
    status: str
    total_products_scraped: int | None


class HealthResponse(BaseModel):
    status: str
    database: str
    last_scrape: LastScrapeResponse | None


class ScrapeRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    scrape_stats: dict | None
    norm_stats: dict | None
    error_message: str | None
