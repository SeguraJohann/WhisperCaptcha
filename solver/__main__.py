import argparse
import asyncio
import logging

from solver import browser, captcha, transcriber


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Solve a reCAPTCHA v2 audio challenge.")
    parser.add_argument("--url", required=True, help="Target page URL (used to derive the captcha origin)")
    parser.add_argument("--sitekey", required=True, help="reCAPTCHA sitekey")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. http://host:port)")
    parser.add_argument("--proxy-user", default=None, dest="proxy_user")
    parser.add_argument("--proxy-password", default=None, dest="proxy_password")
    parser.add_argument("--model", default="small", help="Whisper model (tiny/base/small/medium/large)")
    parser.add_argument("--headless", default=True, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    await browser.start(headless=args.headless)
    await transcriber.load(args.model)
    try:
        token = await captcha.solve(
            url=args.url,
            sitekey=args.sitekey,
            proxy=args.proxy,
            proxy_user=args.proxy_user,
            proxy_password=args.proxy_password,
        )
        print(token)
    finally:
        await browser.stop()


asyncio.run(main())
