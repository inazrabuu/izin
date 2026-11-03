import asyncio
import time
import uuid

import pytest

from helpers import ADMIN, as_principal, post_decision
from izin_server.db import SessionLocal
from izin_server.decisions import write_decision
from izin_server.listener import listener
from izin_server.models import Verdict


@pytest.fixture
async def live_listener():
  listener.start()
  for _ in range(100):
    if listener.connected:
      break
    await asyncio.sleep(0.05)
  assert listener.connected, "the decision listener did not connect"
  yield listener
  await listener.stop()

# ---------- 422 shape ----------

async def test_body_validation_uses_the_izin_shape(client, pending_request, approver):
  r = await client.post(
    f"/v1/inbox/requests/{pending_request['id']}/decision",
    json={"verdict": "deny"},
    headers=as_principal(approver),
  )
  assert r.status_code == 422
  error = r.json()["error"]
  assert (error["code"], error["message"]) == ("invalid_input", "a deny needs a reason")
  assert "detail" not in r.json()

async def test_query_validation_names_the_field(client):
  r = await client.get("/v1/audit/requests", headers=ADMIN)
  assert r.status_code == 422
  fields = {f["field"] for f in r.json()["error"]["fields"]}
  assert {"query.start", "query.end"} <= fields

# ---------- LISTEN / NOTIFY ----------

async def test_commit_wakes_a_subscribed_waiter(live_listener, client, pending_request, approver):
  rid = uuid.UUID(pending_request["id"])
  event = live_listener.subscribe(rid)
  try:
    await post_decision(client, pending_request, approver)
    await asyncio.wait_for(event.wait(), timeout=2)
  finally:
    live_listener.unsubscribe(rid, event)

async def test_rolled_back_decision_wakes_nobody(live_listener, pending_request, approver):
  rid = uuid.UUID(pending_request["id"])
  event = live_listener.subscribe(rid)
  try:
    async with SessionLocal() as s:
      await write_decision(
        s, rid, principal_id=uuid.UUID(approver["id"]), verdict=Verdict.APPROVE, comment=None,
      )
      await s.rollback()
    with pytest.raises(TimeoutError):
      await asyncio.wait_for(event.wait(), timeout=0.5)
  finally:
    live_listener.unsubscribe(rid, event)

async def test_long_poll_is_woken_by_notify(live_listener, client, pending_request, approver):
  async def decide_soon():
    await asyncio.sleep(0.3)
    await post_decision(client, pending_request, approver)

  t0 = time.monotonic()
  poll, _ = await asyncio.gather(
    client.get(f"/v1/requests/{pending_request['id']}/decision", params={"wait": 10}),
    decide_soon(),
  )
  assert poll.json()["status"] == "APPROVED"
  assert time.monotonic() - t0 < 2  # far below the 5s re-check: only a NOTIFY explains this