import os
import uuid

os.environ["IZIN_DATABASE_URL"] = "postgresql+asyncpg://izin:izin@localhost:5432/izin_test"
os.environ["IZIN_API_TOKEN"] = "test-token"

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from helpers import action_payload, request_body

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

@pytest.fixture
async def action(client):
  name = f"refund_order_{uuid.uuid4().hex[:8]}"
  r = await client.put(f"/v1/actions/{name}", json=action_payload())
  assert r.status_code == 200, r.text
  return name

@pytest.fixture
async def approver(client):
  r = await client.post("/v1/principals", json={"name": "Ana", "roles": ["approver"]})
  assert r.status_code == 201, r.text
  return r.json()

@pytest.fixture
async def pending_request(client, action):
  r = await client.post("/v1/requests", json=request_body(action))
  assert r.status_code == 201, r.text
  return r.json()