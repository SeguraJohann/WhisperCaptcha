from playwright.async_api import Browser, Playwright, async_playwright

_playwright: Playwright | None = None
_browser: Browser | None = None


async def start(headless: bool = True) -> None:
    global _playwright, _browser
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=headless)


async def stop() -> None:
    global _playwright, _browser
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    _browser = None
    _playwright = None


def get_browser() -> Browser:
    if _browser is None:
        raise RuntimeError("Browser not started")
    return _browser
