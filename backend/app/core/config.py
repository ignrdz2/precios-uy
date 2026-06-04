from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    scrape_schedule_hour: int = 9
    log_level: str = "INFO"

    model_config = {"env_file": ".env"}


settings = Settings()
