import logging
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.api import admin_router, agent_router, inbox_router
from izin_server.config import settings
from izin_server.db import engine, get_session
from izin_server.errors import IzinError

log = logging.getLogger("izin")

@asynccontextmanager
async def lifespan(app: FastAPI):
  if settings.admin_token == "dev-admin-token" or settings.api_token == "dev-token":
    log.warning(
      "izin is running with default dev tokens. "
      "Set IZIN_ADMIN_TOKEN and IZIN_API_TOKEN before exposing it to the network you don't trust."
    )
  yield
  await engine.dispose()

app = FastAPI(title="izin", version=version("izin-server"), lifespan=lifespan)
app.include_router(admin_router)
app.include_router(agent_router)
app.include_router(inbox_router)

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

if settings.static_dir and Path(settings.static_dir).is_dir():
  app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="inbox")