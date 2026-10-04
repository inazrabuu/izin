import asyncio
import secrets
import uuid

import pytest
from sqlalchemy import func, select

from izin_server.db import SessionLocal
from izin_server.models import Outbox, Request, RequestEvent, Route

REFUND_SCHEMA = {
  "type": "object",
  "required": ["order_id", "amount_idr", "customer"],
  "properties": {
    "order_id": {"type": "string"},
    "amount_idr": {"type": "integer", "minimum": 1},
    "customer": {
      "type": "object",
      "required": ["name"],
      "properties": {"name": {"type": "string"}},
    },
  },
  "additionalProperties": False,
}
ARGS = {"order_id": "ORD-1182", "amount_idr": 4_500_000, "customer": {"name": "Dewi S."}}
HEADLINE = "Refund Rp 4.500.000 to Dewi S. for order ORD-1182."

def action_payload(**overrides):
  payload = {
    "display_name": "Refund order",
    "risk_level": "high",
    "args_schema": REFUND_SCHEMA,
    "render_template": (
      "Refund Rp {{ amount_idr | rupiah }} to {{ customer.name }} "
      "for order {{ order_id }}."
    ),
    "blast_radius": "Rp {{ amount_idr | rupiah }} leaves the company account.",
    "if_denied": "The agent hands the case to customer service"
  }
  payload.update(overrides)
  return payload

@pytest.fixture
async def action(client):
  name = f"refund_order_{uuid.uuid4().hex[:8]}"
  r = await client.put(f"/v1/actions/{name}", json=action_payload())
  assert r.status_code == 200, r.text
  return name


def request_body(action, **overrides):
  body = {
    "action": action,
    "idempotency_key": secrets.token_hex(32),
    "agent_run_id": "run-1",
    "args": ARGS,
    "rationale": "Courier tracking shows delivery failure.",
  }
  body.update(overrides)
  return body


async def count(model, *where):
  async with SessionLocal() as s:
    return await s.scalar(select(func.count()).select_from(model).where(*where))


# ---------- creation ----------

async def test_create_returns_201_with_frozen_screen(client, action):
  r = await client.post("/v1/requests", json=request_body(action))
  assert r.status_code == 201, r.text
  data = r.json()
  assert data["status"] == "PENDING"
  assert data["rendered"]["headline"] == HEADLINE
  assert data["rendered"]["blast_radius"] == "Rp 4.500.000 leaves the company account."


async def test_t1_writes_route_event_and_outbox(client, action):
  r = await client.post("/v1/requests", json=request_body(action))
  rid = uuid.UUID(r.json()["id"])
  assert await count(Route, Route.request_id == rid) == 1
  assert await count(
    RequestEvent, RequestEvent.request_id == rid, RequestEvent.type == "created"
  ) == 1
  assert await count(Outbox, Outbox.request_id == rid) == 1


# ---------- idempotency ----------

async def test_replay_returns_same_request_without_duplicates(client, action):
  body = request_body(action)
  first = await client.post("/v1/requests", json=body)
  second = await client.post("/v1/requests", json=body)

  assert (first.status_code, second.status_code) == (201, 200)
  assert first.json()["id"] == second.json()["id"]

  rid = uuid.UUID(first.json()["id"])
  assert await count(Request, Request.idempotency_key == body["idempotency_key"]) == 1
  assert await count(Outbox, Outbox.request_id == rid) == 1


async def test_replay_with_new_rationale_keeps_the_original(client, action):
  body = request_body(action)
  await client.post("/v1/requests", json=body)
  r = await client.post(
    "/v1/requests", json={**body, "rationale": "A regenerated explanation."}
  )
  assert r.status_code == 200
  assert r.json()["rationale"] == body["rationale"]


async def test_same_key_different_args_is_conflict(client, action):
  body = request_body(action)
  await client.post("/v1/requests", json=body)
  r = await client.post(
    "/v1/requests", json={**body, "args": {**ARGS, "amount_idr": 50_000_000}}
  )
  assert r.status_code == 409
  assert r.json()["error"]["code"] == "idempotency_conflict"


async def test_replay_survives_a_template_change(client, action):
  body = request_body(action)
  await client.post("/v1/requests", json=body)

  # An operator edits the action so it now requires a new field.
  schema = {**REFUND_SCHEMA, "required": [*REFUND_SCHEMA["required"], "reason"]}
  schema["properties"] = {**REFUND_SCHEMA["properties"], "reason": {"type": "string"}}
  r = await client.put(
    f"/v1/actions/{action}",
    json=action_payload(args_schema=schema, render_template="Refund because {{ reason }}."),
  )
  assert r.status_code == 200, r.text

  replay = await client.post("/v1/requests", json=body)
  assert replay.status_code == 200
  assert replay.json()["rendered"]["headline"] == HEADLINE


async def test_concurrent_identical_calls_create_exactly_one(client, action):
  body = request_body(action)
  responses = await asyncio.gather(
    *(client.post("/v1/requests", json=body) for _ in range(10))
  )
  assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
  assert len({r.json()["id"] for r in responses}) == 1

  rid = uuid.UUID(responses[0].json()["id"])
  assert await count(Outbox, Outbox.request_id == rid) == 1


# ---------- atomicity ----------

async def test_t1_is_atomic(client, action, monkeypatch):
  def explode(*args, **kwargs):
    raise RuntimeError("outbox exploded")

  monkeypatch.setattr("izin_server.creation._outbox_payload", explode)
  body = request_body(action)

  with pytest.raises(RuntimeError):
    await client.post("/v1/requests", json=body)

  # The request INSERT ran before the failure, yet nothing survived.
  assert await count(Request, Request.idempotency_key == body["idempotency_key"]) == 0


# ---------- rejection ----------

async def test_unknown_action_is_422(client):
  r = await client.post("/v1/requests", json=request_body("no_such_action"))
  assert r.status_code == 422
  assert r.json()["error"]["code"] == "invalid_input"


async def test_args_violating_schema_is_422(client, action):
  bad = request_body(action, args={**ARGS, "amount_idr": "lots"})
  r = await client.post("/v1/requests", json=bad)
  assert r.status_code == 422
  assert "amount_idr" in r.json()["error"]["message"]


async def test_wrong_token_is_401(client, action):
  r = await client.post(
    "/v1/requests",
    json=request_body(action),
    headers={"Authorization": "Bearer wrong"},
  )
  assert r.status_code == 401


async def test_template_using_optional_field_is_rejected(client):
  name = f"bad_action_{uuid.uuid4().hex[:8]}"
  r = await client.put(
    f"/v1/actions/{name}",
    json=action_payload(render_template="Refund for {{ note }}."),
  )
  assert r.status_code == 422
  assert "note" in r.json()["error"]["message"]