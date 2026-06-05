import asyncio
import logging

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from solver import transcriber
from solver.browser import get_browser

MAX_ATTEMPTS = 3
TOKEN_TIMEOUT_MS = 10_000

log = logging.getLogger(__name__)


class CaptchaError(Exception):
    pass


async def solve(
    url: str,
    sitekey: str,
    proxy: str | None = None,
    proxy_user: str | None = None,
    proxy_password: str | None = None,
) -> str:
    context = await _create_context(proxy, proxy_user, proxy_password)
    try:
        page = await context.new_page()
        await _load_captcha_page(page, url, sitekey)
        log.debug("Captcha page loaded")
        await _open_audio_challenge(page)
        log.debug("Audio challenge opened")
        for attempt in range(MAX_ATTEMPTS):
            log.debug("Attempt %d: downloading audio", attempt + 1)
            audio = await _download_audio(page)
            answer = await transcriber.transcribe(audio)
            log.debug("Transcription: %r", answer)
            token = await _submit_answer(page, answer)
            if token:
                return token
            log.debug("Attempt %d rejected, retrying", attempt + 1)
            if attempt < MAX_ATTEMPTS - 1:
                await _reload_audio(page)
        raise CaptchaError("Failed to solve after maximum attempts")
    except PlaywrightTimeoutError as e:
        raise CaptchaError("Timeout waiting for reCAPTCHA elements") from e
    finally:
        await context.close()


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.7778.96 Safari/537.36"
)


async def _create_context(
    proxy: str | None,
    proxy_user: str | None,
    proxy_password: str | None,
) -> BrowserContext:
    browser = get_browser()
    kwargs: dict = {"user_agent": USER_AGENT}
    if proxy:
        kwargs["proxy"] = {"server": proxy}
        if proxy_user:
            kwargs["proxy"]["username"] = proxy_user
            kwargs["proxy"]["password"] = proxy_password or ""
    context = await browser.new_context(**kwargs)
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return context


async def _load_captcha_page(page: Page, url: str, sitekey: str) -> None:
    html = (
        "<!DOCTYPE html><html><head></head><body>"
        f'<div class="g-recaptcha" data-sitekey="{sitekey}"></div>'
        '<script src="https://www.google.com/recaptcha/api.js" async defer></script>'
        "</body></html>"
    )

    async def serve(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route(url, serve)
    await page.goto(url)
    await asyncio.sleep(2)


async def _open_audio_challenge(page: Page) -> None:
    anchor = page.frame_locator('iframe[src*="recaptcha/api2/anchor"]')
    await anchor.locator("#recaptcha-anchor-label").click()
    await asyncio.sleep(2)
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    await bframe.locator("#recaptcha-audio-button").click()
    await asyncio.sleep(2)


async def _download_audio(page: Page) -> bytes:
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    audio_url = await bframe.locator("#audio-source").get_attribute("src")
    response = await page.context.request.get(audio_url)
    return await response.body()


async def _submit_answer(page: Page, answer: str) -> str | None:
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    await bframe.locator("#audio-response").fill(answer)
    await asyncio.sleep(1)
    await bframe.locator("#recaptcha-verify-button").click()
    await asyncio.sleep(2)
    try:
        await page.wait_for_function(
            "() => document.querySelector('textarea[name=\"g-recaptcha-response\"]').value.length > 0",
            timeout=TOKEN_TIMEOUT_MS,
        )
        return await page.locator('textarea[name="g-recaptcha-response"]').input_value()
    except PlaywrightTimeoutError:
        return None


async def _reload_audio(page: Page) -> None:
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    await bframe.locator("#recaptcha-reload-button").click()
