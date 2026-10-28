import uuid

from sqlalchemy import select

from helpers import ADMIN, ARGS, action_payload, as_principal, request_body
from izin import Approvals, Pending
from izin_server.db import SessionLocal
from izin_server.models import Decision

AMOUNT_DOWN = [{"field": "amount_idr", "only": "decrease"}]

async def put_action(client, editable=AMOUNT_DOWN) -> str:
  name = f"modify_{uuid.uuid4().hex[:8]}"
  r = await client.put(
    f"/v1/actions/{name}", json=action_payload(editable=editable), headers=ADMIN
  )
  assert r.status_code == 200, r.text
  return name

async def create(client, action: str) -> dict:
  r = await client.post("/v1/requests", json=request_body(action))
  assert r.status_code == 201, r.text
  return r.json()

async def modify(client, req, principal, changes, comment="Partial refund only."):
  return await client.post(
    f"/v1/inbox/requests/{req['id']}/decision",
    json={"verdict": "modify", "comment": comment, "changes": changes},
    headers=as_principal(principal),
  )

async def test_modify_approves_with_changed_args(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"amount_idr": 4_000_000})
  assert r.status_code == 200, r.text
  body = r.json()
  assert body["status"] == "APPROVED"
  assert body["decision"]["verdict"] == "modify"
  assert body["decision"]["args"]["amount_idr"] == 4_000_000
  assert body["decision"]["args"]["order_id"] == ARGS["order_id"]
  assert body["decision"]["changes"] == {"amount_idr": {"from": 4_500_000, "to": 4_000_000}}

async def test_decision_keeps_both_screens(client, approver):
  req = await create(client, await put_action(client))
  await modify(client, req, approver, {"amount_idr": 4_000_000})
  async with SessionLocal() as s:
    d = await s.scalar(select(Decision).where(Decision.request_id == uuid.UUID(req["id"])))
  assert d.rendered_snapshot == req["rendered"]
  assert d.modified_screen["headline"] != d.rendered_snapshot["headline"]

async def test_modify_needs_a_reason(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"amount_idr": 4_000_000}, comment="  ")
  assert r.status_code == 422, r.text

async def test_modify_needs_changes(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {})
  assert r.status_code == 422, r.text

async def test_changes_need_the_modify_verdict(client, approver):
  req = await create(client, await put_action(client))
  r = await client.post(
    f"/v1/inbox/requests/{req['id']}/decision",
    json={"verdict": "approve", "changes": {"amount_idr": 1}},
    headers=as_principal(approver),
  )
  assert r.status_code == 422, r.text

async def test_locked_field_is_refused(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"order_id": "ORD-9999"})
  assert r.status_code == 422, r.text
  assert "cannot be changed" in r.json()["error"]["message"]

async def test_decrease_only_refuses_a_raise(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"amount_idr": 5_000_000})
  assert r.status_code == 422, r.text
  assert "only be lowered" in r.json()["error"]["message"]

async def test_unchanged_value_is_refused(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"amount_idr": 4_500_000})
  assert r.status_code == 422, r.text

async def test_changes_are_schema_validated(client, approver):
  req = await create(client, await put_action(client))
  r = await modify(client, req, approver, {"amount_idr": "lots"})
  assert r.status_code == 422, r.text
  assert "args invalid" in r.json()["error"]["message"]

async def test_action_without_editable_refuses_modify(client, approver):
  req = await create(client, await put_action(client, editable=[]))
  r = await modify(client, req, approver, {"amount_idr": 4_000_000})
  assert r.status_code == 422, r.text
  assert "does not allow changes" in r.json()["error"]["message"]

async def test_editable_must_be_a_required_scalar(client):
  r = await client.put(
    f"/v1/actions/bad_{uuid.uuid4().hex[:8]}",
    json=action_payload(editable=[{"field": "customer"}]), headers=ADMIN,
  )
  assert r.status_code == 422, r.text

async def test_direction_needs_a_number(client):
  r = await client.put(
    f"/v1/actions/bad_{uuid.uuid4().hex[:8]}",
    json=action_payload(editable=[{"field": "order_id", "only": "decrease"}]), headers=ADMIN,
  )
  assert r.status_code == 422, r.text

async def test_editable_is_frozen_at_creation(client, approver):
  action = await put_action(client)
  req = await create(client, action)
  r = await client.put(f"/v1/actions/{action}", json=action_payload(editable=[]), headers=ADMIN)
  assert r.status_code == 200, r.text
  r = await modify(client, req, approver, {"amount_idr": 4_000_000})
  assert r.status_code == 200, r.text

async def test_inbox_detail_lists_editable_fields(client, approver):
  req = await create(client, await put_action(client))
  r = await client.get(f"/v1/inbox/requests/{req['id']}", headers=as_principal(approver))
  [field] = r.json()["editable"]
  assert (field["field"], field["only"], field["value"]) == ("amount_idr", "decrease", 4_500_000)

async def test_sdk_receives_modified_args(client, sdk_http, approver):
  action = await put_action(client)
  approvals = Approvals(
    url="http://test", token="test-token",
    agent_run_id=f"run-{uuid.uuid4().hex}", http=sdk_http,
  )
  pending = await approvals.check(action, ARGS)
  assert isinstance(pending, Pending)
  await modify(client, {"id": pending.request_id}, approver, {"amount_idr": 4_000_000})

  decision = await approvals.check(action, ARGS)  # replays the ORIGINAL args
  assert decision.approved and decision.modified
  assert decision.args["amount_idr"] == 4_000_000
  assert decision.changes == {"amount_idr": {"from": 4_500_000, "to": 4_000_000}}