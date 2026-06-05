from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from solver import transcriber
from solver.browser import get_browser

MAX_ATTEMPTS = 3
TOKEN_TIMEOUT_MS = 10_000


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
        await _open_audio_challenge(page)
        for attempt in range(MAX_ATTEMPTS):
            audio = await _download_audio(page)
            answer = await transcriber.transcribe(audio)
            token = await _submit_answer(page, answer)
            if token:
                return token
            if attempt < MAX_ATTEMPTS - 1:
                await _reload_audio(page)
        raise CaptchaError("Failed to solve after maximum attempts")
    except PlaywrightTimeoutError as e:
        raise CaptchaError("Timeout waiting for reCAPTCHA elements") from e
    finally:
        await context.close()


async def _create_context(
    proxy: str | None,
    proxy_user: str | None,
    proxy_password: str | None,
) -> BrowserContext:
    browser = get_browser()
    if not proxy:
        return await browser.new_context()
    proxy_config: dict = {"server": proxy}
    if proxy_user:
        proxy_config["username"] = proxy_user
        proxy_config["password"] = proxy_password or ""
    return await browser.new_context(proxy=proxy_config)


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


async def _open_audio_challenge(page: Page) -> None:
    anchor = page.frame_locator('iframe[src*="recaptcha/api2/anchor"]')
    await anchor.locator("#recaptcha-anchor").click()
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    await bframe.locator("#recaptcha-audio-button").click()


async def _download_audio(page: Page) -> bytes:
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    audio_url = await bframe.locator("audio#audio-source").get_attribute("src")
    response = await page.context.request.get(audio_url)
    return await response.body()


async def _submit_answer(page: Page, answer: str) -> str | None:
    bframe = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]')
    await bframe.locator("#audio-response").fill(answer)
    await bframe.locator("#recaptcha-verify-button").click()
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
