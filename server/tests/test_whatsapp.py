import json
import logging
import uuid

import httpx
import pytest

from helpers import ADMIN, as_principal
from izin_server.assignments import Permanent
from izin_server.config import settings
from izin_server.links import RedactLinkTokens, verify
from izin_server.whatsapp import WhatsAppRetry, send_whatsapp
from test_escalation import create_request, put_action, unique

PHONE = "+6281234567890"

async def setup(client, *, opt_in: bool = True):
  role = unique("role")
  r = await client.post(
    "/v1/principals", json={"name": "Ana", "roles": [role]}, headers=ADMIN
  )
  assert r.status_code == 201, r.text
  ana = r.json()
  if opt_in:
    r = await client.put(
      "/v1/inbox/me/whatsapp", json={"phone": PHONE}, headers=as_principal(ana)
    )
    assert r.status_code == 200, r.text
  action = unique("wa")
  await put_action(client, action, steps=[{"role": role, "after": 3600}])
  return await create_request(client, action), ana

class FakeGraph:
  def __init__(self, status: int = 200, body: dict | None = None):
    self.calls: list[dict] = []
    self.status = status
    self.body = body or {"messages": [{"id": "wamid.TEST"}]}

  def client(self) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
      self.calls.append(json.loads(request.content))
      return httpx.Response(self.status, json=self.body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

@pytest.fixture
def graph(monkeypatch):
  monkeypatch.setattr(settings, "whatsapp_token", "EAAG-test")
  monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1234567890")
  monkeypatch.setattr(settings, "whatsapp_api_url", "https://graph.test/v23.0")

async def send(req, ana, fake: FakeGraph):
  async with fake.client() as http:
    await send_whatsapp(http, uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})

# ---------- redirect and logs ----------

async def test_short_link_redirects_into_the_inbox(client):
  r = await client.get("/l/abc.def-1", follow_redirects=False)
  assert (r.status_code, r.headers["location"]) == (307, "/#/link/abc.def-1")
  assert r.headers["referrer-policy"] == "no-referrer"

def test_access_log_redacts_link_tokens():
  record = logging.LogRecord(
    "uvicorn.access", logging.INFO, __file__, 0,
    '%s - "%s %s HTTP/%s" %d', ("1.2.3.4", "GET", "/l/secret.token", "1.1", 307), None,
  )
  RedactLinkTokens().filter(record)
  assert "secret.token" not in record.getMessage()
  assert "/l/<redacted>" in record.getMessage()

# ---------- opt-in ----------

async def test_opt_in_records_phone_and_consent(client):
  _, ana = await setup(client)
  me = (await client.get("/v1/inbox/me", headers=as_principal(ana))).json()
  assert me["phone"] == PHONE
  assert me["whatsapp_opted_in_at"] is not None

async def test_opt_in_requires_e164(client):
  _, ana = await setup(client, opt_in=False)
  r = await client.put(
    "/v1/inbox/me/whatsapp", json={"phone": "0812-3456-7890"}, headers=as_principal(ana)
  )
  assert r.status_code == 422, r.text

async def test_opt_out_clears_consent(client):
  _, ana = await setup(client)
  r = await client.delete("/v1/inbox/me/whatsapp", headers=as_principal(ana))
  assert (r.status_code, r.json()["whatsapp_opted_in_at"]) == (200, None)

# ---------- sending ----------

async def test_template_carries_no_agent_text(client, graph):
  req, ana = await setup(client)
  fake = FakeGraph()
  await send(req, ana, fake)
  [body] = fake.calls
  assert body["to"] == PHONE.lstrip("+")
  assert req["rendered"]["headline"] not in json.dumps(body)
  body_params = body["template"]["components"][0]["parameters"]
  assert [p["text"] for p in body_params][1] == "medium"
  token = body["template"]["components"][1]["parameters"][0]["text"]
  assert verify(token).principal_id == uuid.UUID(ana["id"])

async def test_without_opt_in_is_permanent(client, graph):
  req, ana = await setup(client, opt_in=False)
  with pytest.raises(Permanent, match="opted in"):
    await send(req, ana, FakeGraph())

async def test_unconfigured_whatsapp_is_permanent(client):
  req, ana = await setup(client)
  with pytest.raises(Permanent, match="not configured"):
    await send(req, ana, FakeGraph())

@pytest.mark.parametrize(
  ("status", "code", "exc"),
  [(400, 190, Permanent), (400, 131056, WhatsAppRetry), (503, None, WhatsAppRetry)],
)
async def test_graph_errors_are_classified_by_code(client, graph, status, code, exc):
  req, ana = await setup(client)
  body = {"error": {"code": code, "message": "nope"}} if code else {}
  with pytest.raises(exc):
    await send(req, ana, FakeGraph(status=status, body=body))