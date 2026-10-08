import secrets
from sqlalchemy import select, func
from izin_server.db import SessionLocal

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

def post_decision(client, req, principal, verdict="approve", comment=None):
  body = {"principal_id": principal["id"], "verdict": verdict}
  if comment is not None:
    body["comment"] = comment
  return client.post(f"/v1/requests/{req['id']}/decision", json=body)