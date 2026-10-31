import uuid

import aiosmtplib
import pytest
from sqlalchemy import select

from helpers import ADMIN
from izin_server import mail
from izin_server.config import settings
from izin_server.db import SessionLocal
from izin_server.errors import Unauthorized
from izin_server.links import mint, verify
from izin_server.models import RequestEvent, Route
from izin_server.timeouts import advance_one
from izin_server.worker import Claimed, deliver_email
from test_escalation import create_request, put_action, unique


def link_header(token: str) -> dict[str, str]:
  return {"Izin-Link": token}

async def principal_with_email(client, role: str, name: str = "Ana") -> dict:
  r = await client.post(
    "/v1/principals",
    json={"name": name, "roles": [role], "email": f"{unique('ana')}@example.test"},
    headers=ADMIN,
  )
  assert r.status_code == 201, r.text
  return r.json()

async def setup(client, risk: str = "medium", afters=(3600,)):
  role = unique("role")
  ana = await principal_with_email(client, role)
  action = unique("mail")
  await put_action(
    client, action, risk_level=risk, steps=[{"role": role, "after": a} for a in afters]
  )
  req = await create_request(client, action)
  return req, ana

async def link_for(req: dict, ana: dict, step: int = 0) -> str:
  async with SessionLocal() as s:
    due = await s.scalar(select(Route.due_at).where(
      Route.request_id == uuid.UUID(req["id"]), Route.step_index == step,
    ))
  return mint(uuid.UUID(req["id"]), uuid.UUID(ana["id"]), step, int(due.timestamp()))

@pytest.fixture
def smtp(monkeypatch):
  sent = []

  async def fake_send(message, **kwargs):
    sent.append(message)

  monkeypatch.setattr(settings, "smtp_host", "smtp.test")
  monkeypatch.setattr(mail.aiosmtplib, "send", fake_send)
  return sent

def test_link_round_trips():
  rid, pid = uuid.uuid4(), uuid.uuid4()
  claims = verify(mint(rid, pid, 2, 4_000_000_000))
  assert (claims.request_id, claims.principal_id, claims.step) == (rid, pid, 2)

def test_tampered_link_is_rejected():
  token = mint(uuid.uuid4(), uuid.uuid4(), 0, 4_000_000_000)
  body, sig = token.split(".")
  with pytest.raises(Unauthorized):
    verify(f"{body}x.{sig}")

def test_expired_link_is_rejected():
  token = mint(uuid.uuid4(), uuid.uuid4(), 0, 1_000)
  with pytest.raises(Unauthorized, match="expired"):
    verify(token, now=1_000)

async def test_link_shows_the_request_without_signing_in(client):
  req, ana = await setup(client)
  r = await client.get("/v1/link", headers=link_header(await link_for(req, ana)))
  assert r.status_code == 200, r.text
  assert (r.json()["id"], r.json()["link_can_decide"]) == (req["id"], True)

async def test_link_decides_as_its_principal(client):
  req, ana = await setup(client)
  r = await client.post(
    "/v1/link/decision", json={"verdict": "approve"},
    headers=link_header(await link_for(req, ana)),
  )
  assert r.status_code == 200, r.text
  assert r.json()["decision"]["principal_name"] == "Ana"
  async with SessionLocal() as s:
    payload = await s.scalar(select(RequestEvent.payload).where(
      RequestEvent.request_id == uuid.UUID(req["id"]), RequestEvent.type == "decided",
    ))
  assert payload["via"] == "link"

async def test_link_cannot_decide_high_risk(client):
  req, ana = await setup(client, risk="high")
  headers = link_header(await link_for(req, ana))
  assert (await client.get("/v1/link", headers=headers)).json()["link_can_decide"] is False
  r = await client.post("/v1/link/decision", json={"verdict": "approve"}, headers=headers)
  assert r.status_code == 403, r.text

async def test_link_for_an_earlier_step_is_refused(client):
  req, ana = await setup(client, afters=(0, 3600))  # same role at both steps
  token = await link_for(req, ana, step=0)
  token = mint(uuid.UUID(req["id"]), uuid.UUID(ana["id"]), 0, 4_000_000_000)  # keep it unexpired
  assert await advance_one(uuid.UUID(req["id"])) == "escalated"
  r = await client.post("/v1/link/decision", json={"verdict": "approve"}, headers=link_header(token))
  assert r.status_code == 409, r.text
  assert "earlier step" in r.json()["error"]["message"]

async def test_second_use_of_a_link_is_refused(client):
  req, ana = await setup(client)
  headers = link_header(await link_for(req, ana))
  first = await client.post("/v1/link/decision", json={"verdict": "approve"}, headers=headers)
  second = await client.post("/v1/link/decision", json={"verdict": "approve"}, headers=headers)
  assert (first.status_code, second.status_code) == (200, 409)

async def test_missing_link_is_401(client):
  assert (await client.get("/v1/link")).status_code == 401

async def test_email_carries_the_headline_and_a_working_link(client, smtp):
  req, ana = await setup(client)
  await mail.send_assignment(uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})
  [msg] = smtp
  assert msg["To"] == ana["email"]
  body = msg.get_content()
  assert req["rendered"]["headline"] in body
  token = body.split("#/link/")[1].split()[0]
  assert verify(token).principal_id == uuid.UUID(ana["id"])

async def test_email_for_a_decided_request_is_skipped(client, smtp):
  req, ana = await setup(client)
  await client.post(
    "/v1/link/decision", json={"verdict": "approve"},
    headers=link_header(await link_for(req, ana)),
  )
  item = Claimed(
    id=0, request_id=uuid.UUID(req["id"]), channel="email",
    payload={"step": 0}, attempts=1, principal_id=uuid.UUID(ana["id"]),
  )
  outcome = await deliver_email(item)
  assert (outcome.ok, outcome.skipped, smtp) == (True, True, [])

@pytest.mark.parametrize(("code", "permanent"), [(550, True), (451, False)])
async def test_smtp_reply_codes_decide_retry(client, smtp, monkeypatch, code, permanent):
  async def refuse(message, **kwargs):
    raise aiosmtplib.SMTPResponseException(code, "nope")

  monkeypatch.setattr(mail.aiosmtplib, "send", refuse)
  req, ana = await setup(client)
  item = Claimed(
    id=0, request_id=uuid.UUID(req["id"]), channel="email",
    payload={"step": 0}, attempts=1, principal_id=uuid.UUID(ana["id"]),
  )
  outcome = await deliver_email(item)
  assert (outcome.ok, outcome.permanent) == (False, permanent)