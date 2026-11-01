import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from helpers import ADMIN, as_principal, post_decision, request_body
from izin_server.db import SessionLocal
from izin_server.timeouts import advance_one
from test_escalation import put_action, unique

AMOUNT_DOWN = [{"field": "amount_idr", "only": "decrease"}]

def window() -> list[tuple[str, str]]:
  now = datetime.now(UTC)
  return [("start", (now - timedelta(hours=1)).isoformat()), ("end", (now + timedelta(hours=1)).isoformat())]

async def make_request(client, action: str, amount: int, rationale: str = "Courier lost it.") -> dict:
  body = request_body(action)
  body["args"] = {**body["args"], "amount_idr": amount}
  body["idempotency_key"] = uuid.uuid4().hex * 2
  body["rationale"] = rationale
  r = await client.post("/v1/requests", json=body)
  assert r.status_code == 201, r.text
  return r.json()

async def export(client, action: str, *extra: tuple[str, str], fmt: str = "jsonl"):
  params = [*window(), ("action", action), ("format", fmt), *extra]
  return await client.get("/v1/audit/requests", params=params, headers=ADMIN)

def rows(r) -> list[dict]:
  return [json.loads(line) for line in r.text.splitlines()]

async def refund_action(client) -> str:
  action = unique("audit")
  await put_action(client, action, editable=AMOUNT_DOWN)
  return action

async def modify_down(client, req, approver, amount: int):
  r = await client.post(
    f"/v1/inbox/requests/{req['id']}/decision",
    json={"verdict": "modify", "comment": "Partial refund.", "changes": {"amount_idr": amount}},
    headers=as_principal(approver),
  )
  assert r.status_code == 200, r.text

async def test_filter_uses_the_approved_amount(client, approver):
  action = await refund_action(client)
  small = await make_request(client, action, 4_500_000)
  modified = await make_request(client, action, 6_000_000)
  large = await make_request(client, action, 7_000_000)
  await post_decision(client, small, approver)
  await modify_down(client, modified, approver, 4_000_000)
  await post_decision(client, large, approver)

  r = await export(client, action, ("arg", "amount_idr>=5000000"))
  assert r.status_code == 200, r.text
  assert [row["request_id"] for row in rows(r)] == [large["id"]]

async def test_rows_show_requested_and_approved_args(client, approver):
  action = await refund_action(client)
  req = await make_request(client, action, 6_000_000)
  await modify_down(client, req, approver, 4_000_000)
  [row] = rows(await export(client, action))
  assert row["requested_args"]["amount_idr"] == 6_000_000
  assert row["approved_args"]["amount_idr"] == 4_000_000
  assert row["changes"] == {"amount_idr": {"from": 6_000_000, "to": 4_000_000}}
  assert (row["verdict"], row["decided_by"], row["decided_via"], row["automatic"]) == (
    "modify", "Ana", "inbox", False,
  )

async def test_undecided_requests_export_as_pending(client):
  action = await refund_action(client)
  await make_request(client, action, 1_000_000)
  [row] = rows(await export(client, action))
  assert (row["status"], row["verdict"], row["approved_args"]) == ("PENDING", None, None)

async def test_automatic_decisions_are_marked(client):
  action = unique("audit")
  await put_action(client, action, default_timeout=0)
  req = await make_request(client, action, 1_000_000)
  await advance_one(uuid.UUID(req["id"]))
  [row] = rows(await export(client, action))
  assert (row["automatic"], row["decided_via"], row["decided_by"]) == (True, "timeout", "izin (timeout)")

async def test_csv_neutralises_formulas(client):
  action = await refund_action(client)
  await make_request(client, action, 1_000_000, rationale='=HYPERLINK("https://evil","x")')
  r = await export(client, action, fmt="csv")
  [row] = list(csv.DictReader(io.StringIO(r.text)))
  assert row["rationale"].startswith("'=")

async def test_export_is_recorded_with_its_hash(client):
  action = await refund_action(client)
  await make_request(client, action, 1_000_000)
  r = await export(client, action, fmt="csv")
  record = await client.get(f"/v1/audit/exports/{r.headers['x-izin-export-id']}", headers=ADMIN)
  assert record.status_code == 200, record.text
  assert record.json()["sha256"] == hashlib.sha256(r.content).hexdigest()
  assert record.json()["row_count"] == 1

async def test_export_records_are_append_only(client):
  action = await refund_action(client)
  r = await export(client, action)
  async with SessionLocal() as s:
    try:
      await s.execute(
        text("UPDATE audit_exports SET sha256 = 'forged' WHERE id = :id"),
        {"id": r.headers["x-izin-export-id"]},
      )
      await s.commit()
      raise AssertionError("an export record was edited")
    except DBAPIError:
      await s.rollback()

async def test_naive_timestamps_are_rejected(client):
  r = await client.get(
    "/v1/audit/requests",
    params={"start": "2026-04-01T00:00:00", "end": "2026-07-01T00:00:00"},
    headers=ADMIN,
  )
  assert r.status_code == 422, r.text
  assert "timezone" in r.json()["error"]["message"]

async def test_malformed_arg_filter_is_rejected(client):
  r = await export(client, "anything", ("arg", "amount_idr~5"))
  assert r.status_code == 422, r.text

async def test_text_filters_only_support_equality(client):
  r = await export(client, "anything", ("arg", "order_id>ORD"))
  assert r.status_code == 422, r.text

async def test_audit_needs_the_admin_token(client):
  r = await client.get("/v1/audit/requests", params=window())
  assert r.status_code in (401, 403), r.text