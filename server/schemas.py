from typing import Literal

from pydantic import BaseModel


class SolveRequest(BaseModel):
    url: str
    sitekey: str
    proxy: str | None = None
    proxy_user: str | None = None
    proxy_password: str | None = None


class SolveSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    token: str


class SolveError(BaseModel):
    status: Literal["error"] = "error"
    message: str
