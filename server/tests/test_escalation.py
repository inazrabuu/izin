import asyncio
import uuid

from sqlalchemy import select

from helpers import (
  ADMIN,
  ARGS,
  action_payload,
  as_principal,
  count,
  post_decision,
  request_body,
)
from izin import Approvals, Pending
from izin_server.db import SessionLocal
from izin_server.models import RequestEvent, Route, Verdict
from izin_server.timeouts import advance_one


def unique(prefix: str) -> str:
  return f"{prefix}_{uuid.uuid4().hex[:8]}"

async def make_principal(client, name: str, role: str) -> dict:
  r = await client.post("/v1/principals", json={"name": name, "roles": [role]}, headers=ADMIN)
  assert r.status_code == 201, r.text
  return r.json()

async def put_action(client, name: str, **overrides) -> None:
  overrides.setdefault("risk_level", "medium")
  r = await client.put(f"/v1/actions/{name}", json=action_payload(**overrides), headers=ADMIN)
  assert r.status_code == 200, r.text

async def create_request(client, action: str) -> dict:
  r = await client.post("/v1/requests", json=request_body(action))
  assert r.status_code == 201, r.text
  return r.json()

async def chain(client, *afters: int) -> tuple[str, list[dict]]:
  """An action routed through fresh roles, one principal per step."""
  roles = [unique("role") for _ in afters]
  people = [await make_principal(client, f"P{i}", role) for i, role in enumerate(roles)]
  action = unique("chain")
  await put_action(
    client, action, steps=[{"role": role, "after": after} for role, after in zip(roles, afters)]
  )
  return action, people

async def inbox_ids(client, principal) -> set[str]:
  r = await client.get("/v1/inbox/requests", headers=as_principal(principal))
  assert r.status_code == 200, r.text
  return {item["id"] for item in r.json()}

async def status(client, rid) -> str:
  return (await client.get(f"/v1/requests/{rid}/decision")).json()["status"]

async def event_types(rid: uuid.UUID) -> list[str]:
  async with SessionLocal() as s:
    return list(await s.scalars(
      select(RequestEvent.type).where(RequestEvent.request_id == rid).order_by(RequestEvent.id)
    ))

async def test_due_step_escalates_to_the_next_role(client):
  action, _ = await chain(client, 0, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  assert await advance_one(rid) == "escalated"
  assert await status(client, rid) == "PENDING"
  assert await count(Route, Route.request_id == rid) == 2

async def test_inbox_moves_to_the_next_approver(client):
  action, (ana, budi) = await chain(client, 0, 3600)
  req = await create_request(client, action)
  assert req["id"] in await inbox_ids(client, ana)
  await advance_one(uuid.UUID(req["id"]))
  assert req["id"] not in await inbox_ids(client, ana)
  assert req["id"] in await inbox_ids(client, budi)

async def test_stale_tap_after_escalation_is_a_conflict(client):
  action, (ana, _) = await chain(client, 0, 3600)
  req = await create_request(client, action)
  await advance_one(uuid.UUID(req["id"]))
  r = await post_decision(client, req, ana)
  assert r.status_code == 409, r.text
  assert "escalated" in r.json()["error"]["message"]

async def test_escalated_approver_decides(client):
  action, (_, budi) = await chain(client, 0, 3600)
  req = await create_request(client, action)
  await advance_one(uuid.UUID(req["id"]))
  r = await post_decision(client, req, budi)
  assert r.status_code == 200, r.text
  assert r.json()["decision"]["principal_name"] == "P1"

async def test_earlier_approver_can_still_view(client):
  action, (ana, _) = await chain(client, 0, 3600)
  req = await create_request(client, action)
  await advance_one(uuid.UUID(req["id"]))
  r = await client.get(f"/v1/inbox/requests/{req['id']}", headers=as_principal(ana))
  assert r.status_code == 200, r.text

async def test_last_step_applies_on_timeout(client):
  action, _ = await chain(client, 0, 0)
  rid = uuid.UUID((await create_request(client, action))["id"])
  assert await advance_one(rid) == "escalated"
  assert await advance_one(rid) == Verdict.DENY
  assert await event_types(rid) == ["created", "escalated", "expired", "decided"]

async def test_two_sweepers_escalate_once(client):
  action, _ = await chain(client, 0, 3600)
  rid = uuid.UUID((await create_request(client, action))["id"])
  results = await asyncio.gather(advance_one(rid), advance_one(rid))
  assert sorted(map(str, results)) == ["None", "escalated"]
  assert await count(Route, Route.request_id == rid) == 2

async def test_plan_is_frozen_at_creation(client):
  action = unique("frozen")
  await put_action(client, action, default_timeout=0)
  rid = uuid.UUID((await create_request(client, action))["id"])
  await put_action(client, action, default_timeout=0, risk_level="low", on_timeout="approve")
  assert await advance_one(rid) == Verdict.DENY

async def test_sdk_keeps_waiting_through_escalation(client, sdk_http):
  action, _ = await chain(client, 0, 3600)
  approvals = Approvals(
    url="http://test", token="test-token", agent_run_id=unique("run"), http=sdk_http
  )
  first = await approvals.check(action, ARGS)
  await advance_one(uuid.UUID(first.request_id))
  second = await approvals.check(action, ARGS)
  assert isinstance(second, Pending)
  assert second.request_id == first.request_id

async def test_steps_and_default_timeout_are_exclusive(client):
  r = await client.put(
    f"/v1/actions/{unique('bad')}",
    json=action_payload(default_timeout=60, steps=[{"role": "x", "after": 60}]),
    headers=ADMIN,
  )
  assert r.status_code == 422, r.text

async def test_step_needs_a_target(client):
  r = await client.put(
    f"/v1/actions/{unique('bad')}", json=action_payload(steps=[{"after": 60}]), headers=ADMIN
  )
  assert r.status_code == 422, r.text

async def test_escalate_is_no_longer_an_on_timeout(client):
  r = await client.put(
    f"/v1/actions/{unique('bad')}", json=action_payload(on_timeout="escalate"), headers=ADMIN
  )
  assert r.status_code == 422, r.text