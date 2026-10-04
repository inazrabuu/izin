import os

os.environ["IZIN_DATABASE_URL"] = "postgresql+asyncpg://izin:izin@localhost:5432/izin_test"
os.environ["IZIN_API_TOKEN"] = "test-token"

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

SERVER_DIR = Path(__file__).resolve().parent.parent

def _migrate_fresh() -> None:
  cfg = Config(str(SERVER_DIR / "alembic.ini"))
  cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
  command.downgrade(cfg, "base")
  command.upgrade(cfg, "head")

@pytest.fixture(scope="session")
def fresh_database():
  with ThreadPoolExecutor(max_workers=1) as pool:
    pool.submit(_migrate_fresh).result()

@pytest.fixture(scope="session")
async def engine_lifecycle(fresh_database):
  yield
  from izin_server.db import engine

  await engine.dispose()

@pytest.fixture
async def client(engine_lifecycle):
  from izin_server.main import app

  async with AsyncClient(
    transport=ASGITransport(app=app),
    base_url="http://test",
    headers={"Authorization": "Bearer test-token"},
  ) as c:
    yield c