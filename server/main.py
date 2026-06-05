from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from server.auth import verify_api_key
from server.config import settings
from server.schemas import SolveError, SolveRequest, SolveSuccess
from solver import browser, captcha, transcriber


@asynccontextmanager
async def lifespan(_: FastAPI):
    await browser.start(headless=settings.browser_headless)
    await transcriber.load(settings.whisper_model)
    yield
    await browser.stop()


app = FastAPI(lifespan=lifespan)


@app.post("/solve", dependencies=[Depends(verify_api_key)])
async def solve(request: SolveRequest) -> SolveSuccess | SolveError:
    try:
        token = await captcha.solve(
            url=request.url,
            sitekey=request.sitekey,
            proxy=request.proxy,
            proxy_user=request.proxy_user,
            proxy_password=request.proxy_password,
        )
        return SolveSuccess(token=token)
    except Exception as e:
        return SolveError(message=str(e))
