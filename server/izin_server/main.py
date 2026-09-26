from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.db import engine, get_session

@asynccontextmanager
async def lifespan(app: FastAPI):
  yield
  await engine.dispose()

app = FastAPI(title="izin", version="0.1.0", lifespan=lifespan)

@app.get("/healthz")
async def healthz(response: Response, session: AsyncSession = Depends(get_session)):
  try:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
  except Exception:
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "degraded", "db": "unreachable"}