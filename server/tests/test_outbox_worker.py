import asyncio
import json
import uuid

import httpx
from sqlalchemy import func, select, update

from helpers import count, request_body
from izin_server import worker
from izin_server.db import SessionLocal
from izin_server.models import Outbox, OutboxStatus, RequestEvent
from izin_server.webhooks import verify

URL = "https://receiver.test/hook"
SECRET = "test-webhook-secret"

class Receiver:
  def __init__(self, status: int = 204):
    self.status = status
    self.calls: list[httpx.Request] = []

  def _handle(self, request: httpx.Request) -> httpx.Request:
    self.calls.append(request)
    return httpx.Response(self.status)

  def client(self) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

  def for_request(self, request_id: str) -> list[httpx.Request]:
    return [c for c in self.calls if json.loads(c.content)["request_id"] == request_id]

async def drain(http, url=URL, batch_size=50):
  while await worker.run_once(http, url=url, secret=SECRET, batch_size=batch_size):
    pass

async def outbox_row(request_id: str) -> Outbox:
  async with SessionLocal() as s:
    return await s.scalar(select(Outbox).where(Outbox.request_id == uuid.UUID(request_id)))

async def test_delivers_a_signed_webhook(pending_request):
  receiver = Receiver()
  async with receiver.client() as http:
    await drain(http)

  [call] = receiver.for_request(pending_request["id"])
  assert call.url == URL
  assert verify(SECRET, call.headers, call.content)
  body = json.loads(call.content)
  assert body["headline"] == pending_request["rendered"]["headline"]
  assert "args" not in body

  row = await outbox_row(pending_request["id"])
  assert (row.status, row.attempts) == (OutboxStatus.SENT, 1)
  assert call.headers["Izin-Delivery-Id"] == str(row.id)
  assert await count(
    RequestEvent, RequestEvent.request_id == row.request_id, RequestEvent.type == "notified"
  ) == 1

async def test_server_error_is_retried_later(pending_request):
  async with Receiver(status=500).client() as http:
    await drain(http)

  row = await outbox_row(pending_request["id"])
  assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
  assert "500" in row.last_error
  async with SessionLocal() as s:
    assert await s.scalar(select(Outbox.next_attempt_at > func.now()).where(Outbox.id == row.id))

async def test_client_error_fails_immediately(pending_request):
  async with Receiver(status=400).client() as http:
    await drain(http)

  row = await outbox_row(pending_request["id"])
  assert(row.status, row.attempts) == (OutboxStatus.FAILED, 1)
  assert await count(
    RequestEvent,
    RequestEvent.request_id == row.request_id,
    RequestEvent.type == "notify_failed"
  ) == 1

async def test_gives_up_after_max_attempt(pending_request):
  rid = uuid.UUID(pending_request["id"])
  async with SessionLocal() as s:
    await s.execute(
      update(Outbox).where(Outbox.request_id == rid).values(attempts=worker.MAX_ATTEMPTS - 1)
    )
    await s.commit()

  async with Receiver(status=500).client() as http:
    await drain(http)

  row = await outbox_row(pending_request["id"])
  assert (row.status, row.attempts) == (OutboxStatus.FAILED, worker.MAX_ATTEMPTS)

async def test_unconfigured_webhook_fails_visibility(pending_request):
  async with Receiver().client() as http:
    await drain(http, url=None)

  row = await outbox_row(pending_request["id"])
  assert row.status == OutboxStatus.FAILED
  assert "not configured" in row.last_error

async def test_concurrent_workers_deliver_each_row_once(client, action):
  ids = []
  for _ in range(6):
    r = await client.post("/v1/requests", json=request_body(action))
    ids.append(r.json()["id"])

  receiver = Receiver()
  async with receiver.client() as http:
    await asyncio.gather(*(drain(http, batch_size=2) for _ in range(3)))

  delivery_ids = [c.headers["Izin-Delivery-Id"] for c in receiver.calls]
  assert len(delivery_ids) == len(set(delivery_ids))
  for rid in ids:
    assert len(receiver.for_request(rid)) == 1

async def test_stale_worker_cannot_overwrite_the_outcome(pending_request):
  rid = uuid.UUID(pending_request["id"])

  async with SessionLocal() as s:
    [first] = [c for c in await worker.claim(s, 1000) if c.request_id == rid]
    await s.commit()

  async with SessionLocal() as s:
    await s.execute(update(Outbox).where(Outbox.id == first.id).values(next_attempt_at=func.now()))
    await s.commit()

  async with SessionLocal() as s:
    [second] = [c for c in await worker.claim(s, 1000) if c.request_id == rid]
    await s.commit()

  await worker.record_outcome(first, worker.Outcome(ok=True))

  row = await outbox_row(pending_request["id"])
  assert row.status == OutboxStatus.PENDING
  assert row.attempts == second.attempts == first.attempts + 1