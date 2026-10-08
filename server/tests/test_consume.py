import uuid

from helpers import count, post_decision
from izin_server.models import RequestEvent

def consume(client, req):
  return client.get(f"/v1/requests/{req['id']}/consume")

async def test_consume_after_approval(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  r = await consume(client, pending_request)
  assert r.status_code == 200, r.text
  assert r.json()["status"] == "CONSUMED"
  assert r.json()["decision"]["verdict"] == "approve"

async def test_consume_is_idempotent(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  first = await consume(client, pending_request)
  second = await consume(client, pending_request)
  assert (first.status_code, second.status_code) == (200, 200), second.text
  rid = uuid.UUID(pending_request["id"])
  assert await count(
    RequestEvent, RequestEvent.request_id == rid, RequestEvent.type == "consumed"
  ) == 1

async def test_consume_before_decision_is_409(client, pending_request):
  r = await consume(client, pending_request)
  assert r.status_code == 409, r.text
  assert r.json()["error"]["code"] == "not_decided"

async def test_consume_unknown_is_404(client):
  r = await client.post(f"/v1/requests/{uuid.uuid4()}/consume")
  assert r.status_code == 404

async def test_decision_still_readable_after_consume(client, pending_request, approver):
  await post_decision(client, pending_request, approver)
  await consume(client, pending_request)
  r = await client.get(f"/v1/requests/{pending_request["id"]}/decision")
  assert r.json()["status"] == "CONSUMED"
  assert r.json()["decision"]["verdict"] == "approve"