import logging
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from fastapi import Path as PathParam
from fastapi import Request as HTTPRequest
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.api import (
  admin_router,
  agent_router,
  inbox_router,
  link_router,
  slack_router,
)
from izin_server.config import settings
from izin_server.db import get_session
from izin_server.errors import IzinError
from izin_server.links import RedactLinkTokens
from izin_server.listener import listener

log = logging.getLogger("izin")
logging.getLogger("uvicorn.access").addFilter(RedactLinkTokens())

@asynccontextmanager
async def lifespan(app: FastAPI):
  if settings.admin_token == "dev-admin-token" or settings.api_token == "dev-token":
    log.warning(
      "izin is running with default dev tokens. "
      "Set IZIN_ADMIN_TOKEN and IZIN_API_TOKEN before exposing it to the network you don't trust."
    )
  listener.start()
  yield
  await listener.stop()

app = FastAPI(title="izin", version=version("izin-server"), lifespan=lifespan)
app.include_router(admin_router)
app.include_router(agent_router)
app.include_router(inbox_router)
app.include_router(link_router)
app.include_router(slack_router)

@app.get("/healthz")
async def healthz(response: Response, session: Annotated[AsyncSession, Depends(get_session)]):
  try:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
  except Exception:
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    log.warning("health check: database unreachable", exc_info=True)
    return {"status": "degraded", "db": "unreachable"}

@app.exception_handler(IzinError)
async def handle_izin_error(_: HTTPRequest, exc: IzinError) -> JSONResponse:
  return JSONResponse(
    status_code=exc.status_code,
    content={
      "error": {
        "code": exc.code,
        "message": exc.message
      }
    }
  )  

@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: HTTPRequest, exc: RequestValidationError) -> JSONResponse:
  fields = []
  for err in exc.errors():
    field = ".".join(str(p) for p in err["loc"] if p != "body") or "(body)"
    fields.append({"field": field, "message": err["msg"].removeprefix("Value error, ")})
  first = fields[0] if fields else {"field": "(request)", "message": "invalid request"}
  message = first["message"] if first["field"] == "(body)" else f"{first['field']}: {first['message']}"
  return JSONResponse(
    status_code=422,
    content={"error": {"code": "invalid_input", "message": message, "fields": fields}},
  )

@app.get("/l/{token}", include_in_schema=False)
async def open_short_link(token: str = PathParam(pattern=r"^[\w.-]+$")) -> RedirectResponse:
  return RedirectResponse(
    f"/#/link/{token}",
    status_code=307,
    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
  )

if settings.static_dir and Path(settings.static_dir).is_dir():
  app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="inbox")