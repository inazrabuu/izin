import asyncio
import time
import uuid

from sqlalchemy import select

from helpers import count
from izin_server.db import SessionLocal
from izin_server.models import Decision, Request, RequestEvent

def post_decision(client, req, principal, verdict="approve", comment=None):
  body = {"principal_id": principal["id"], "verdict": verdict}
  if comment is not None:
    body["comment"] = comment
  return client.post(f"/v1/requests/{req['id']}/decision", json=body)

def poll_url(req):
  return f"/v1/requests/{req['id']}/decision"

async def test_approve(client, pending_request, approver):
  r = await post_decision(client, pending_request, approver)
  assert r.status_code == 200, r.text
  data = r.json()
  assert data["status"] == "APPROVED"
  assert data["decision"]["verdict"] == "approve"  
  assert data["decision"]["principal_name"] == "Ana"
  assert data["decision"]["args"] == pending_request["args"]

async def test_desicion_snapshots_what_the_approver_saw(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  rid = uuid.UUID(pending_request["id"])
  async with SessionLocal() as s:
    decision = await s.scalar(select(Decision).where(Decision.request_id == rid))
  assert decision.rendered_snapshot == pending_request["rendered"]
  assert await count(
    RequestEvent, RequestEvent.request_id == rid, RequestEvent.type == "decided"
  ) == 1

async def test_deny_requires_a_reason(client, pending_request, approver):
  r = await post_decision(client, pending_request, approver, verdict="deny")
  assert r.status_code == 422

  r = await post_decision(
    client, pending_request, approver, verdict="deny",
    comment="Tracking shows it was delivered"
  )
  assert r.status_code == 200
  assert r.json()["status"] == "DENIED"

async def test_second_decision_says_who_decided(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  r = await post_decision(client, pending_request, approver, verdict="deny", comment="Too late")
  assert r.status_code == 409
  assert r.json()["error"]["code"] == "already_decided"
  assert "Ana" in r.json()["error"]["message"]

async def test_concurrent_decisions_first_write_wins(client, pending_request, approver):
  a, b = await asyncio.gather(
    post_decision(client, pending_request, approver, verdict="approve"),
    post_decision(client, pending_request, approver, verdict="deny", comment="No")
  )
  assert sorted([a.status_code, b.status_code]) == [200, 409]
  winner = a if a.status_code == 200 else b

  rid = uuid.UUID(pending_request["id"])
  assert await count(Decision, Decision.request_id == rid) == 1
  async with SessionLocal() as s:
    status = await s.scalar(select(Request.status).where(Request.id == rid))
  assert status == winner.json()["status"]

async def test_non_approver_is_forbidden(client, pending_request):
  r = await client.post("/v1/principals", json={"name": "Budi", "roles": ["developer"]})
  r = await post_decision(client, pending_request, r.json())
  assert r.status_code == 403

async def test_modify_is_not_in_v01(client, pending_request, approver):
  r = await post_decision(client, pending_request, approver, verdict="modify")
  assert r.status_code == 422

async def test_unknown_request_is_404(client, approver):
  r = await client.post(
    f"/v1/requests/{uuid.uuid4()}/decision",
    json={"principal_id": approver["id"], "verdict": "approve"}
  )
  assert r.status_code == 404, r.text

async def test_poll_without_wait_returns_pending(client, pending_request):
  r = await client.get(poll_url(pending_request))
  assert r.status_code == 200
  assert r.json()["status"] == "PENDING"
  assert r.json()["decision"] is None

async def test_poll_returns_immediately_when_already_decided(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  t0 = time.monotonic()
  r = await client.get(poll_url(pending_request), params={"wait": 30})
  assert time.monotonic() - t0 < 1
  assert r.json()["decision"]["verdict"] == "approve"

async def test_long_poll_wakes_up_on_decision(client, pending_request, approver):
  async def decide_later():
    await asyncio.sleep(0.3)
    return await post_decision(client, pending_request, approver)

  t0 = time.monotonic()
  poll, _ = await asyncio.gather(
    client.get(poll_url(pending_request), params={"wait": 10}),
    decide_later()
  )
  assert poll.json()["decision"]["verdict"] == "approve"
  assert time.monotonic() - t0 < 2.5

async def test_long_poll_does_not_hold_db_connection(client, pending_request, approver):
  waiters = [
    asyncio.create_task(client.get(poll_url(pending_request), params={"wait": 3}))
    for _ in range(25)
  ]
  await asyncio.sleep(0.2)

  r = await asyncio.wait_for(post_decision(client, pending_request, approver), timeout=2)
  assert r.status_code == 200

  results = await asyncio.gather(*waiters)
  assert all(x.json()["decision"] is not None for x in results)

async def test_wait_is_bounded(client, pending_request):
  r = await client.get(poll_url(pending_request), params={"wait": 31})
  assert r.status_code == 422