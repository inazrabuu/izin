import asyncio
import uuid

import pytest
from helpers import ADMIN, ARGS, action_payload, count, post_decision, request_body
from izin import Approvals, Pending
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from izin_server.db import SessionLocal
from izin_server.models import (
  Action,
  Decision,
  OnTimeout,
  RequestEvent,
  RiskLevel,
  Verdict,
)
from izin_server.system import TIMEOUT_PRINCIPAL_ID
from izin_server.timeouts import advance_one, sweep_once


async def make_action(client, **overrides) -> str:
  name = f"timeout_{uuid.uuid4().hex[:8]}"
  r = await client.put(f"/v1/actions/{name}", json=action_payload(**overrides), headers=ADMIN)
  assert r.status_code == 200, r.text
  return name

async def expired_request(client, **overrides) -> uuid.UUID:
  overrides.setdefault("risk_level", "medium")
  action = await make_action(client, default_timeout=0, **overrides)
  r = await client.post("/v1/requests", json=request_body(action))
  assert r.status_code == 201, r.text
  return uuid.UUID(r.json()["id"])

async def view(client, rid) -> dict:
  r = await client.get(f"/v1/requests/{rid}/decision")
  assert r.status_code == 200, r.text
  return r.json()

async def test_timeout_deny_records_a_system_decision(client):
  rid = await expired_request(client)
  assert await advance_one(rid) == Verdict.DENY
  v = await view(client, rid)
  assert v["status"] == "DENIED"
  assert v["decision"]["principal_name"] == "izin (timeout)"
  assert v["decision"]["comment"].startswith("Denied automatically")

async def test_low_risk_timeout_can_auto_approval(client):
  rid = await expired_request(client, risk_level="low", on_timeout="approve")
  assert await advance_one(rid) == Verdict.APPROVE
  assert (await view(client, rid))["status"] == "APPROVED"

async def test_events_show_expired_then_decided(client):
  rid = await expired_request(client)
  await advance_one(rid)
  async with SessionLocal() as s:
    types = list(await s.scalars(
      select(RequestEvent.type).where(RequestEvent.request_id == rid).order_by(RequestEvent.id)
    ))
  assert types == ["created", "expired", "decided"]

async def test_second_sweep_is_a_noop(client):
  rid = await expired_request(client)
  assert await advance_one(rid) is not None
  assert await advance_one(rid) is None
  assert await count(Decision, Decision.request_id == rid) == 1

async def test_two_sweepers_race_one_decision(client):
  rid = await expired_request(client)
  results = await asyncio.gather(advance_one(rid), advance_one(rid))
  assert sorted(r is None for r in results) == [False, True]
  assert await count(Decision, Decision.request_id == rid) == 1

async def test_not_yet_due_is_left_alone(client, pending_request):
  rid = uuid.UUID(pending_request["id"])
  assert await advance_one(rid) is None
  assert (await view(client, rid))["status"] == "PENDING"

async def test_human_after_the_deadline_is_refused(client, approver):
  rid = await expired_request(client)
  r = await post_decision(client, {"id": str(rid)}, approver)
  assert r.status_code == 409, r.text
  assert "expired" in r.json()["error"]["message"]

async def test_sweep_once_closes_due_requests(client):
  rid = await expired_request(client)
  assert await sweep_once() >= 1
  assert (await view(client, rid))["status"] == "DENIED"

async def test_api_rejects_risky_auto_approve(client):
  r = await client.put(
    "/v1/actions/risky_auto",
    json=action_payload(risk_level="critical", on_timeout="approve"),
    headers=ADMIN,
  )
  assert r.status_code == 422, r.text
  assert r.json()["error"]["code"] == "invalid_input"

async def test_db_rejects_risky_auto_approve(client):
  async with SessionLocal() as s:
    s.add(Action(
      name=f"raw_{uuid.uuid4().hex[:8]}", display_name="Raw", render_template="x",
      risk_level=RiskLevel.HIGH, on_timeout=OnTimeout.APPROVE,
    ))
    with pytest.raises(IntegrityError, match="no_auto_approve_risky"):
      await s.commit()

async def test_system_principal_cannot_hold_a_token(client):
  r = await client.post(f"/v1/principals/{TIMEOUT_PRINCIPAL_ID}/token", headers=ADMIN)
  assert r.status_code == 403, r.text

async def test_sdk_receives_the_timeout_decision(client, sdk_http):
  action = await make_action(client, default_timeout=0, risk_level="medium")
  approvals = Approvals(
    url="http://test", token="test-token",
    agent_run_id=f"run-{uuid.uuid4().hex}", http=sdk_http,
  )
  pending = await approvals.check(action, ARGS)
  assert isinstance(pending, Pending)

  await advance_one(uuid.UUID(pending.request_id))

  decision = await approvals.check(action, ARGS)
  assert not decision.approved
  assert decision.decided_by == "izin (timeout)"