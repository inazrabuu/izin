import uuid

from helpers import ADMIN, ARGS, as_principal, post_decision
from izin import Approvals
from sqlalchemy import func, select
from test_escalation import (
  chain,
  create_request,
  event_types,
  make_principal,
  put_action,
  unique,
)

from izin_server.db import SessionLocal
from izin_server.models import RequestEvent, Route, Verdict
from izin_server.system import TIMEOUT_PRINCIPAL_ID
from izin_server.timeouts import advance_one


async def set_available(client, principal: dict, available: bool) -> None:
  r = await client.put(
    f"/v1/principals/{principal['id']}/availability",
    json={"available": available}, headers=ADMIN,
  )
  assert r.status_code == 200, r.text

async def current_step(rid: uuid.UUID) -> int:
  async with SessionLocal() as s:
    return await s.scalar(select(func.max(Route.step_index)).where(Route.request_id == rid))

async def decision_of(client, rid) -> dict:
  return (await client.get(f"/v1/requests/{rid}/decision")).json()

async def test_away_first_step_is_skipped_at_creation(client):
  action, (p0, _) = await chain(client, 3600, 3600)
  await set_available(client, p0, False)
  rid = uuid.UUID((await create_request(client, action))["id"])
  assert await current_step(rid) == 1
  assert await event_types(rid) == ["created", "skipped"]

async def test_skipped_event_names_who_was_away(client):
  action, (p0, _) = await chain(client, 3600, 3600)
  await set_available(client, p0, False)
  rid = uuid.UUID((await create_request(client, action))["id"])
  async with SessionLocal() as s:
    payload = await s.scalar(
      select(RequestEvent.payload)
      .where(RequestEvent.request_id == rid, RequestEvent.type == "skipped")
    )
  assert payload["step"] == 0
  assert payload["unavailable"] == [{"id": p0["id"], "name": "P0"}]

async def test_nobody_available_denies_at_creation(client):
  action, (p0,) = await chain(client, 3600)
  await set_available(client, p0, False)
  req = await create_request(client, action)
  v = await decision_of(client, req["id"])
  assert v["status"] == "DENIED"
  assert v["decision"]["principal_name"] == "izin (routing)"

async def test_sdk_gets_the_routing_denial_immediately(client, sdk_http):
  action, (p0,) = await chain(client, 3600)
  await set_available(client, p0, False)
  approvals = Approvals(
    url="http://test", token="test-token", agent_run_id=unique("run"), http=sdk_http
  )
  decision = await approvals.check(action, ARGS)
  assert not decision.approved
  assert decision.decided_by == "izin (routing)"

async def test_escalation_skips_an_away_step(client):
  action, (_, p1, _) = await chain(client, 0, 3600, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await set_available(client, p1, False)  # goes away AFTER creation
  assert await advance_one(rid) == "escalated"
  assert await current_step(rid) == 2
  assert await event_types(rid) == ["created", "skipped", "escalated"]

async def test_escalation_with_nobody_left_denies(client):
  action, (_, p1) = await chain(client, 0, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await set_available(client, p1, False)
  assert await advance_one(rid) == Verdict.DENY
  assert (await decision_of(client, rid))["decision"]["principal_name"] == "izin (routing)"

async def test_away_principal_keeps_authority(client):
  role = unique("role")
  ana = await make_principal(client, "Ana", role)
  await make_principal(client, "Budi", role)
  action = unique("pool")
  await put_action(client, action, steps=[{"role": role, "after": 3600}])
  await set_available(client, ana, False)
  req = await create_request(client, action)  # Budi is available, so the step is not skipped
  r = await post_decision(client, req, ana)   # ...and Ana may still decide
  assert r.status_code == 200, r.text

async def test_principal_sets_own_availability(client, approver):
  headers = as_principal(approver)
  r = await client.put("/v1/inbox/me/availability", json={"available": False}, headers=headers)
  assert r.status_code == 200, r.text
  assert r.json() == {"available": False}
  assert (await client.get("/v1/inbox/me", headers=headers)).json()["available"] is False
  await client.put("/v1/inbox/me/availability", json={"available": True}, headers=headers)

async def test_admin_sets_availability(client, approver):
  await set_available(client, approver, False)
  await set_available(client, approver, True)

async def test_system_principals_have_no_availability(client):
  r = await client.put(
    f"/v1/principals/{TIMEOUT_PRINCIPAL_ID}/availability",
    json={"available": True}, headers=ADMIN,
  )
  assert r.status_code == 403, r.text

async def test_agent_token_cannot_set_availability(client):
  r = await client.put("/v1/inbox/me/availability", json={"available": False})
  assert r.status_code == 401, r.text