import uuid

import pytest
from sqlalchemy import select, update
from test_availability import set_available

from helpers import ADMIN, as_principal, post_decision
from izin_server.config import settings
from izin_server.db import SessionLocal
from izin_server.models import Outbox, OutboxStatus, RequestEvent
from izin_server.notify import emit
from izin_server.timeouts import advance_one
from test_escalation import chain, create_request, make_principal, put_action, unique


async def rows(rid, channel: str = "webhook") -> list[Outbox]:
  async with SessionLocal() as s:
    return list(await s.scalars(
      select(Outbox)
      .where(Outbox.request_id == uuid.UUID(str(rid)), Outbox.channel == channel)
      .order_by(Outbox.id)
    ))

async def events(rid, channel: str = "webhook") -> list[str]:
  return [r.event for r in await rows(rid, channel)]

async def fail(row_id: int, attempts: int = 3) -> None:
  async with SessionLocal() as s:
    await s.execute(
      update(Outbox).where(Outbox.id == row_id)
      .values(status=OutboxStatus.FAILED, attempts=attempts, last_error="HTTP 500")
    )
    await s.commit()

@pytest.fixture
def human_channel(monkeypatch):
  monkeypatch.setattr(settings, "human_channels", ["test"])

async def test_human_decision_emits_decided(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  assert await events(pending_request["id"]) == ["request.created", "request.decided"]
  decided = (await rows(pending_request["id"]))[-1].payload
  assert (decided["verdict"], decided["automatic"]) == ("approve", False)

async def test_timeout_emits_an_automatic_decision(client):
  action, _ = await chain(client, 0)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await advance_one(rid)
  decided = (await rows(rid))[-1].payload
  assert (decided["event"], decided["verdict"], decided["automatic"]) == (
    "request.decided", "deny", True,
  )

async def test_escalation_emits_escalated(client):
  action, _ = await chain(client, 0, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await advance_one(rid)
  assert await events(rid) == ["request.created", "request.escalated"]
  assert (await rows(rid))[-1].payload["to_step"] == 1

async def test_unroutable_emits_created_then_decided(client):
  action, (p0,) = await chain(client, 3600)
  await set_available(client, p0, False)
  req = await create_request(client, action)
  assert await events(req["id"]) == ["request.created", "request.decided"]

async def test_webhook_payload_is_thin(client, pending_request):
  [created] = await rows(pending_request["id"])
  assert set(created.payload) == {
    "event", "request_id", "agent_run_id", "action", "risk_level", "at",
  }

async def test_enqueue_is_idempotent(client, pending_request):
  rid = uuid.UUID(pending_request["id"])
  async with SessionLocal() as s:
    await emit(s, rid, "request.created")
    await emit(s, rid, "request.created")
    await s.commit()
  assert await events(rid) == ["request.created"]

async def test_assignment_goes_to_available_approvers_only(client, human_channel):
  role = unique("role")
  ana = await make_principal(client, "Ana", role)
  budi = await make_principal(client, "Budi", role)
  await set_available(client, budi, False)
  action = unique("fanout")
  await put_action(client, action, steps=[{"role": role, "after": 3600}])
  req = await create_request(client, action)
  assigned = await rows(req["id"], channel="test")
  assert [str(r.principal_id) for r in assigned] == [ana["id"]]

async def test_escalation_assigns_the_next_step(client, human_channel):
  action, (_, p1) = await chain(client, 0, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await advance_one(rid)
  assigned = await rows(rid, channel="test")
  assert [(r.payload["step"], str(r.principal_id)) for r in assigned][-1] == (1, p1["id"])

async def test_admin_lists_failed_deliveries(client, pending_request):
  [row] = await rows(pending_request["id"])
  await fail(row.id)
  r = await client.get("/v1/outbox", params={"status": "failed"}, headers=ADMIN)
  assert r.status_code == 200, r.text
  assert row.id in {item["id"] for item in r.json()}

async def test_retry_requeues_the_same_row(client, pending_request):
  [row] = await rows(pending_request["id"])
  await fail(row.id, attempts=3)
  r = await client.post(f"/v1/outbox/{row.id}/retry", headers=ADMIN)
  assert r.status_code == 200, r.text
  body = r.json()
  assert (body["id"], body["status"], body["attempts"], body["give_up_after"]) == (
    row.id, "pending", 3, 11,
  )
  async with SessionLocal() as s:
    retried = await s.scalar(select(RequestEvent.payload).where(
      RequestEvent.request_id == row.request_id, RequestEvent.type == "notify_retried",
    ))
  assert retried["previous_error"] == "HTTP 500"

async def test_only_failed_deliveries_can_be_retried(client, pending_request):
  [row] = await rows(pending_request["id"])
  r = await client.post(f"/v1/outbox/{row.id}/retry", headers=ADMIN)
  assert r.status_code == 422, r.text

async def test_retry_needs_the_admin_token(client, pending_request, approver):
  [row] = await rows(pending_request["id"])
  r = await client.post(f"/v1/outbox/{row.id}/retry", headers=as_principal(approver))
  assert r.status_code in (401, 403), r.text