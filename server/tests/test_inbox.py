import hashlib
import uuid

from helpers import ADMIN, as_principal, post_decision, request_body
from izin_server.db import SessionLocal
from izin_server.models import Principal


async def create_outsider(client) -> dict:
  r = await client.post("/v1/principals", json={"name": "Budi", "roles": ["developer"]}, headers=ADMIN)
  return r.json()

# ---------- scopes ----------

async def test_agent_token_cannot_decide(client, pending_request):
  r = await client.post(
    f"/v1/inbox/requests/{pending_request['id']}/decision", json={"verdict": "approve"}
  )
  assert r.status_code == 401, r.text

async def test_agent_token_cannot_create_principals(client):
  r = await client.post("/v1/principals", json={"name": "Eve", "roles": ["approver"]})
  assert r.status_code == 401, r.text

async def test_inbox_token_cannot_create_requests(client, action, approver):
  r = await client.post("/v1/requests", json=request_body(action), headers=as_principal(approver))
  assert r.status_code == 401, r.text

# ---------- tokens ----------

async def test_me(client, approver):
  r = await client.get("/v1/inbox/me", headers=as_principal(approver))
  assert r.status_code == 200, r.text
  assert r.json()["name"] == "Ana"
  assert "token" not in r.json() and "token_hash" not in r.json()

async def test_tokens_are_stored_hashed(approver):
  async with SessionLocal() as s:
    principal = await s.get(Principal, uuid.UUID(approver["id"]))
  assert principal.token_hash == hashlib.sha256(approver["token"].encode()).hexdigest()

async def test_rotating_a_token_revokes_the_old_one(client, approver):
  r = await client.post(f"/v1/principals/{approver['id']}/token", headers=ADMIN)
  assert r.status_code == 200, r.text
  new_token = r.json()["token"]

  old = await client.get("/v1/inbox/me", headers=as_principal(approver))
  new = await client.get("/v1/inbox/me", headers={"Authorization": f"Bearer {new_token}"})
  assert (old.status_code, new.status_code) == (401, 200)

  # ---------- list ----------

async def test_inbox_lists_only_what_you_can_decide(client, pending_request, approver):
  mine = await client.get("/v1/inbox/requests", headers=as_principal(approver))
  assert pending_request["id"] in [i["id"] for i in mine.json()]

  outsider = await create_outsider(client)
  theirs = await client.get("/v1/inbox/requests", headers=as_principal(outsider))
  assert pending_request["id"] not in [i["id"] for i in theirs.json()]


async def test_decided_requests_leave_the_inbox(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  r = await client.get("/v1/inbox/requests", headers=as_principal(approver))
  assert pending_request["id"] not in [i["id"] for i in r.json()]

  # ---------- detail ----------

async def test_detail_has_everything_the_screen_needs(client, pending_request, approver):
  r = await client.get(
    f"/v1/inbox/requests/{pending_request['id']}",
    headers=as_principal(approver)
  )
  assert r.status_code == 200, r.text
  d = r.json()
  assert d["rendered"] == pending_request["rendered"]
  assert d["action_display_name"] == "Refund order"
  assert d["risk_level"] == "high"
  assert d["rationale"] == pending_request["rationale"]
  assert d["decision"] is None

async def test_similar_approvals_this_week(client, action, approver):
  first = (await client.post("/v1/requests", json=request_body(action))).json()
  await post_decision(client, first, approver)

  second = (await client.post("/v1/requests", json=request_body(action))).json()
  r = await client.get(f"/v1/inbox/requests/{second['id']}", headers=as_principal(approver))
  assert r.json()["similar_approved_7d"] == 1

async def test_outsider_cannot_open_a_request(client, pending_request):
  outsider = await create_outsider(client)
  r = await client.get(
    f"/v1/inbox/requests/{pending_request['id']}", headers=as_principal(outsider)
  )
  assert r.status_code == 403