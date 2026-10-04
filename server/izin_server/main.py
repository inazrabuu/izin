from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response, status
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.db import engine, get_session
from izin_server.api import router
from izin_server.errors import IzinError

@asynccontextmanager
async def lifespan(app: FastAPI):
  yield
  await engine.dispose()

app = FastAPI(title="izin", version="0.1.0", lifespan=lifespan)
app.include_router(router)

@app.get("/healthz")
async def healthz(response: Response, session: AsyncSession = Depends(get_session)):
  try:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
  except Exception:
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
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