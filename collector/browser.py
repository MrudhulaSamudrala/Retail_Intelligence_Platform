"""Playwright browser session helpers: retries, timeouts, screenshots."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

T = TypeVar("T")
logger = logging.getLogger("collector.browser")


DEFAULT_TIMEOUT_MS = int(os.getenv("COLLECTION_TIMEOUT_MS", "30000"))
DEFAULT_RETRIES = int(os.getenv("COLLECTION_RETRIES", "3"))
SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", "data/screenshots"))


def _default_user_agent() -> str:
    return os.getenv(
        "USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )


class BrowserSession:
    """Shared Playwright lifecycle used by all retailer adapters."""

    def __init__(
        self,
        *,
        headless: bool | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        screenshot_dir: Path | None = None,
    ) -> None:
        if headless is None:
            headless = os.getenv("COLLECTION_HEADLESS", "true").lower() in {
                "1",
                "true",
                "yes",
            }
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.screenshot_dir = screenshot_dir or SCREENSHOT_DIR
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserSession":
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self.context = await self._browser.new_context(
            user_agent=_default_user_agent(),
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
        )
        self.context.set_default_timeout(self.timeout_ms)
        await self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self.context:
            await self.context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> Page:
        if not self.context:
            raise RuntimeError("BrowserSession is not started")
        return await self.context.new_page()

    async def goto(
        self,
        page: Page,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                await page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
                return
            except Exception as exc:  # noqa: BLE001 - retry boundary
                last_error = exc
                logger.warning(
                    "navigation_failed",
                    extra={
                        "event": "navigation_failed",
                        "url": url,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(min(2 ** attempt, 8))
        assert last_error is not None
        raise last_error

    async def screenshot(
        self,
        page: Page,
        *,
        label: str,
        full_page: bool = False,
    ) -> str | None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:80]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.screenshot_dir / f"{stamp}_{safe}.png"
        try:
            await page.screenshot(path=str(path), full_page=full_page)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "screenshot_failed",
                extra={"event": "screenshot_failed", "error": str(exc), "url": page.url},
            )
            return None


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = DEFAULT_RETRIES,
    label: str = "operation",
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "retryable_failure",
                extra={
                    "event": "retryable_failure",
                    "attempt": attempt,
                    "error": str(exc),
                    "url": label,
                },
            )
            await asyncio.sleep(min(2 ** attempt, 8))
    assert last_error is not None
    raise last_error
