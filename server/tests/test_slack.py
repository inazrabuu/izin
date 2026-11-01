import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from helpers import ADMIN, post_decision
from izin_server import slack
from izin_server.assignments import Permanent
from izin_server.config import settings
from izin_server.db import SessionLocal
from izin_server.models import Outbox, RequestEvent, SlackMessage
from izin_server.slack import SlackRetry, post_assignment, update_message, verify_slack
from test_escalation import create_request, put_action, unique

SECRET = "test-slack-secret"

def slack_id() -> str:
  return "U" + uuid.uuid4().hex[:10].upper()

async def setup(client, *, linked: bool = True):
  role = unique("role")
  r = await client.post(
    "/v1/principals",
    json={"name": "Ana", "roles": [role], "slack_user_id": slack_id() if linked else None},
    headers=ADMIN,
  )
  assert r.status_code == 201, r.text
  action = unique("slack")
  await put_action(client, action, steps=[{"role": role, "after": 3600}])
  return await create_request(client, action), r.json()

def signed(payload: dict, *, ts: int | None = None):
  body = urlencode({"payload": json.dumps(payload)}).encode()
  ts = int(time.time()) if ts is None else ts
  sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
  return body, {
    "X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig,
    "Content-Type": "application/x-www-form-urlencoded",
  }

def click(req, ana, action_id="approve"):
  return {
    "type": "block_actions", "user": {"id": ana["slack_user_id"]}, "trigger_id": "trig-1",
    "response_url": "https://hooks.slack.com/actions/T/1/x",
    "actions": [{"action_id": action_id, "value": json.dumps({"r": req["id"], "s": 0})}],
  }

def deny_submission(req, ana, reason):
  return {
    "type": "view_submission", "user": {"id": ana["slack_user_id"]},
    "view": {
      "callback_id": "izin_deny",
      "private_metadata": json.dumps({"r": req["id"], "s": 0}),
      "state": {"values": {"reason": {"reason": {"value": reason}}}},
    },
  }

async def interact(client, payload):
  body, headers = signed(payload)
  return await client.post("/v1/slack/interactions", content=body, headers=headers)

async def decided_payload(rid):
  async with SessionLocal() as s:
    return await s.scalar(select(RequestEvent.payload).where(
      RequestEvent.request_id == uuid.UUID(rid), RequestEvent.type == "decided",
    ))

@pytest.fixture
def slack_env(monkeypatch):
  calls = {"ephemeral": [], "api": []}

  async def fake_ephemeral(http, url, message):
    calls["ephemeral"].append(message)

  async def fake_call(http, method, body):
    calls["api"].append((method, body))
    return {"ok": True}

  monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
  monkeypatch.setattr(slack, "post_ephemeral", fake_ephemeral)
  monkeypatch.setattr(slack, "slack_call", fake_call)
  return calls

class FakeSlack:
  def __init__(self, response: dict | None = None, status: int = 200):
    self.calls: list = []
    self.status = status
    self.response = response or {
      "ok": True, "channel": "D123", "ts": f"{uuid.uuid4().int % 10**10}.000100",
    }

  def client(self) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
      self.calls.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
      return httpx.Response(self.status, json=self.response)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

@pytest.fixture
def slack_api(monkeypatch):
  monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
  monkeypatch.setattr(settings, "slack_api_url", "https://slack.test/api")

# ---------- signatures ----------

def test_signature_round_trips(monkeypatch):
  monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
  body, headers = signed({"type": "x"})
  assert verify_slack(headers, body)

def test_tampered_body_fails(monkeypatch):
  monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
  body, headers = signed({"type": "x"})
  assert not verify_slack(headers, body + b"x")

def test_stale_timestamp_fails(monkeypatch):
  monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
  body, headers = signed({"type": "x"}, ts=int(time.time()) - 600)
  assert not verify_slack(headers, body)

def test_mrkdwn_is_escaped():
  assert slack.esc("<!channel> & <https://x|y>") == "&lt;!channel&gt; &amp; &lt;https://x|y&gt;"

# ---------- interactions ----------

async def test_unsigned_interaction_is_rejected(client, slack_env):
  r = await client.post(
    "/v1/slack/interactions", content=b"payload=%7B%7D",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
  )
  assert r.status_code == 401, r.text

async def test_approve_click_decides_as_the_linked_principal(client, slack_env):
  req, ana = await setup(client)
  r = await interact(client, click(req, ana))
  assert r.status_code == 200, r.text
  assert slack_env["ephemeral"] == []
  assert (await decided_payload(req["id"]))["via"] == "slack"

async def test_unlinked_slack_user_is_told_and_nothing_is_decided(client, slack_env):
  req, ana = await setup(client)
  await interact(client, click(req, {**ana, "slack_user_id": slack_id()}))
  assert "isn't linked" in slack_env["ephemeral"][0]
  assert await decided_payload(req["id"]) is None

async def test_deny_click_opens_a_modal_and_decides_nothing(client, slack_env):
  req, ana = await setup(client)
  await interact(client, click(req, ana, "deny"))
  [(method, body)] = slack_env["api"]
  assert (method, body["trigger_id"], body["view"]["callback_id"]) == (
    "views.open", "trig-1", "izin_deny",
  )
  assert await decided_payload(req["id"]) is None

async def test_deny_submission_decides_with_the_reason(client, slack_env):
  req, ana = await setup(client)
  r = await interact(client, deny_submission(req, ana, "Tracking shows delivered."))
  assert (r.status_code, r.content) == (200, b"")
  status = (await client.get(f"/v1/requests/{req['id']}/decision")).json()
  assert (status["status"], status["decision"]["comment"]) == ("DENIED", "Tracking shows delivered.")

async def test_blank_reason_is_shown_in_the_modal(client, slack_env):
  req, ana = await setup(client)
  r = await interact(client, deny_submission(req, ana, "   "))
  assert r.json() == {"response_action": "errors", "errors": {"reason": "A reason is required."}}

async def test_second_click_is_told_who_decided(client, slack_env):
  req, ana = await setup(client)
  await interact(client, click(req, ana))
  await interact(client, click(req, ana))
  assert slack_env["ephemeral"] == ["Already approved by Ana"]

# ---------- outbound ----------

async def test_assignment_posts_a_dm_and_remembers_it(client, slack_api):
  req, ana = await setup(client)
  fake = FakeSlack()
  async with fake.client() as http:
    await post_assignment(http, uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})
  [(method, body)] = fake.calls
  assert (method, body["channel"]) == ("chat.postMessage", ana["slack_user_id"])
  assert any(b["type"] == "actions" for b in body["blocks"])
  async with SessionLocal() as s:
    msg = await s.scalar(
      select(SlackMessage).where(SlackMessage.request_id == uuid.UUID(req["id"]))
    )
  assert (msg.channel, msg.ts) == ("D123", fake.response["ts"])

async def test_decision_refreshes_the_slack_message(client, slack_api):
  req, ana = await setup(client)
  fake = FakeSlack()
  async with fake.client() as http:
    await post_assignment(http, uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})
    r = await post_decision(client, req, ana)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
      row = await s.scalar(select(Outbox).where(
        Outbox.request_id == uuid.UUID(req["id"]), Outbox.channel == "slack",
      ))
    await update_message(http, row.payload)
  method, body = fake.calls[-1]
  assert method == "chat.update"
  assert "Approved by Ana" in body["text"]
  assert not any(b["type"] == "actions" for b in body["blocks"])

@pytest.mark.parametrize(
  ("status", "error", "exc"),
  [(200, "invalid_auth", Permanent), (429, "ratelimited", SlackRetry)],
)
async def test_slack_errors_are_classified(client, slack_api, status, error, exc):
  req, ana = await setup(client)
  fake = FakeSlack(response={"ok": False, "error": error}, status=status)
  async with fake.client() as http:
    with pytest.raises(exc):
      await post_assignment(http, uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})

async def test_unlinked_principal_is_permanent(client, slack_api):
  req, ana = await setup(client, linked=False)
  async with FakeSlack().client() as http:
    with pytest.raises(Permanent, match="no Slack account"):
      await post_assignment(http, uuid.UUID(req["id"]), uuid.UUID(ana["id"]), {"step": 0})

async def test_admin_links_a_slack_account(client):
  _, ana = await setup(client, linked=False)
  sid = slack_id()
  r = await client.put(
    f"/v1/principals/{ana['id']}/slack", json={"slack_user_id": sid}, headers=ADMIN
  )
  assert (r.status_code, r.json()["slack_user_id"]) == (200, sid)