import argparse
import asyncio

import httpx
from playwright.async_api import async_playwright


async def run(page_url: str, server_url: str, api_key: str | None) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await page.route("**/recaptcha/api.js**", lambda route: route.abort())
        await page.goto(page_url)

        sitekey = await page.locator("div.g-recaptcha[data-sitekey]").get_attribute(
            "data-sitekey"
        )
        print(f"[demo] sitekey: {sitekey}")
        print("[demo] delegating to WhisperCaptcha server...")

        token = await _fetch_token(server_url, page_url, sitekey, api_key)
        print(f"[demo] token: {token[:40]}...")

        await page.evaluate(
            """(token) => {
                let el = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (!el) {
                    el = Object.assign(document.createElement('textarea'), { name: 'g-recaptcha-response' });
                    document.body.appendChild(el);
                }
                el.value = token;
            }""",
            token,
        )
        print("[demo] token injected into page.")

        input("[demo] press Enter to close the browser...")
        await browser.close()


async def _fetch_token(
    server_url: str, page_url: str, sitekey: str, api_key: str | None
) -> str:
    headers = {"X-API-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{server_url}/solve",
            json={"url": page_url, "sitekey": sitekey},
            headers=headers,
        )
    data = response.json()
    if data["status"] != "ok":
        raise RuntimeError(data["message"])
    return data["token"]


def main() -> None:
    parser = argparse.ArgumentParser(description="WhisperCaptcha end-to-end demo.")
    parser.add_argument("--url", required=True, help="Target page URL with a reCAPTCHA v2 widget")
    parser.add_argument("--server", default="http://localhost:8000", help="WhisperCaptcha server URL")
    parser.add_argument("--api-key", default=None, dest="api_key", help="API key (if the server requires one)")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.server, args.api_key))


if __name__ == "__main__":
    main()
