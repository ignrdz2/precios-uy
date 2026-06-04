import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from playwright.async_api import BrowserContext, Playwright

logger = logging.getLogger(__name__)


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
    _USER_AGENTS: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ]

    def __init__(self, supermarket_slug: str) -> None:
        self.supermarket_slug = supermarket_slug

    @abstractmethod
    async def scrape_all(self) -> list[ScrapedProduct]:
        """Navigate the site and return all found products."""
        ...

    @abstractmethod
    async def scrape_category(self, category_url: str) -> list[ScrapedProduct]:
        """Scrape a specific category URL."""
        ...

    async def _get_browser_context(self, playwright: Playwright) -> BrowserContext:
        """Launch a headless Chromium browser and return a context with a rotated user-agent."""
        user_agent = random.choice(self._USER_AGENTS)
        browser = await playwright.chromium.launch(headless=True)
        return await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1280, "height": 720},
            locale="es-UY",
        )

    async def _safe_scrape_with_retry(
        self,
        fn: Callable[[], Coroutine[Any, Any, list[ScrapedProduct]]],
        max_retries: int = 3,
    ) -> list[ScrapedProduct]:
        """Call fn() up to max_retries times with exponential backoff (1s, 2s, 4s).

        Returns an empty list if all attempts fail — a partial scrape is valid.
        """
        for attempt in range(max_retries):
            try:
                return await fn()
            except Exception as exc:
                wait = 2**attempt  # 1s → 2s → 4s
                logger.warning(
                    "[%s] attempt %d/%d failed: %s. Retrying in %ds.",
                    self.supermarket_slug,
                    attempt + 1,
                    max_retries,
                    exc,
                    wait,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)

        logger.error(
            "[%s] all %d attempts failed, returning empty list.",
            self.supermarket_slug,
            max_retries,
        )
        return []

    async def _random_delay(self) -> None:
        """Sleep 1–3 seconds to mimic human browsing behavior."""
        delay = random.uniform(1.0, 3.0)
        logger.debug("[%s] waiting %.1fs before next request.", self.supermarket_slug, delay)
        await asyncio.sleep(delay)
