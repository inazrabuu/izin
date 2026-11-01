import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.assignments import Assignment, Permanent, load_assignment
from izin_server.config import settings
from izin_server.db import SessionLocal
from izin_server.decisions import decide, load_view
from izin_server.errors import IzinError
from izin_server.models import Principal, Request, SlackMessage, Verdict
from izin_server.schemas import DecisionStatusOut, InboxDecisionIn

log = logging.getLogger("izin.slack")

TOLERANCE_SECONDS = 300
PERMANENT_ERRORS = {
  "invalid_auth", "not_authed", "account_inactive", "token_revoked", "missing_scope",
  "channel_not_found", "user_not_found", "message_not_found", "cant_update_message",
  "invalid_blocks", "msg_too_long",
}

class SlackRetry(Exception):
  """A Slack failure worth retrying."""

# ---------- inbound: verify and handle clicks ----------

def verify_slack(headers: Mapping[str, str], body: bytes, now: float | None = None) -> bool:
  if not settings.slack_signing_secret:
    return False
  try:
    ts = int(headers["X-Slack-Request-Timestamp"])
    signature = headers["X-Slack-Signature"]
  except (KeyError, ValueError):
    return False
  if abs((time.time() if now is None else now) - ts) > TOLERANCE_SECONDS:
    return False
  base = f"v0:{ts}:".encode() + body
  expected = "v0=" + hmac.new(
    settings.slack_signing_secret.encode(), base, hashlib.sha256
  ).hexdigest()
  return hmac.compare_digest(expected, signature)

async def post_ephemeral(http: httpx.AsyncClient, response_url: str, message: str) -> None:
  if not response_url.startswith("https://hooks.slack.com/"):
    log.warning("ignoring unexpected response_url %r", response_url)
    return
  try:
    await http.post(
      response_url,
      json={"response_type": "ephemeral", "replace_original": False, "text": message},
    )
  except httpx.HTTPError:
    log.warning("could not reply to Slack", exc_info=True)

async def _decide(
  session: AsyncSession, principal: Principal, target: dict[str, Any],
  verdict: Verdict, comment: str | None = None,
) -> str | None:
  """Decide as `principal`. Returns an error message for Ana, or None on success."""
  try:
    body = InboxDecisionIn(verdict=verdict, comment=comment)
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await decide(
      session, uuid.UUID(target["r"]), principal, body, step=int(target["s"]), via="slack"
    )
  except ValidationError:
    return "A reason is required."
  except IzinError as e:
    await session.rollback()
    return e.message
  except DBAPIError:
    log.exception("slack decision failed")
    await session.rollback()
    return "izin is busy right now; please try again."
  return None

def deny_modal(value: str) -> dict[str, Any]:
  return {
    "type": "modal",
    "callback_id": "izin_deny",
    "private_metadata": value,
    "title": {"type": "plain_text", "text": "Deny request"},
    "submit": {"type": "plain_text", "text": "Deny"},
    "close": {"type": "plain_text", "text": "Cancel"},
    "blocks": [{
      "type": "input",
      "block_id": "reason",
      "label": {"type": "plain_text", "text": "Why? The agent receives your reason."},
      "element": {"type": "plain_text_input", "action_id": "reason", "multiline": True},
    }],
  }

async def handle_interaction(
  session: AsyncSession, http: httpx.AsyncClient, payload: dict[str, Any]
) -> dict[str, Any] | None:
  slack_user = payload.get("user", {}).get("id")
  principal = (
    await session.scalar(select(Principal).where(Principal.slack_user_id == slack_user))
    if slack_user else None
  )
  not_linked = "Your Slack account isn't linked to izin. Ask an admin to link it."

  if payload.get("type") == "block_actions":
    action = payload["actions"][0]
    if action["action_id"] == "open":
      return None  # a URL button; Slack still notifies us, nothing to do
    if principal is None:
      await post_ephemeral(http, payload["response_url"], not_linked)
      return None
    if action["action_id"] == "approve":
      error = await _decide(session, principal, json.loads(action["value"]), Verdict.APPROVE)
      if error:
        await post_ephemeral(http, payload["response_url"], error)
    elif action["action_id"] == "deny":
      try:
        await slack_call(http, "views.open", {
          "trigger_id": payload["trigger_id"], "view": deny_modal(action["value"]),
        })
      except (Permanent, SlackRetry, httpx.HTTPError):
        log.warning("could not open the deny form", exc_info=True)
        await post_ephemeral(
          http, payload["response_url"], "Couldn't open the form; please deny from the inbox."
        )
    return None

  if payload.get("type") == "view_submission" and payload["view"]["callback_id"] == "izin_deny":
    if principal is None:
      return {"response_action": "errors", "errors": {"reason": not_linked}}
    reason = payload["view"]["state"]["values"]["reason"]["reason"]["value"]
    target = json.loads(payload["view"]["private_metadata"])
    error = await _decide(session, principal, target, Verdict.DENY, reason)
    return {"response_action": "errors", "errors": {"reason": error}} if error else None

  return None

# ---------- outbound: post and update messages ----------

def esc(raw: str) -> str:
  """Slack mrkdwn treats <...> as links and mentions; agent-written text must not."""
  return raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def slack_call(http: httpx.AsyncClient, method: str, body: dict[str, Any]) -> dict[str, Any]:
  if not settings.slack_bot_token:
    raise Permanent("slack_bot_token is not configured")
  r = await http.post(
    f"{settings.slack_api_url}/{method}",
    json=body,
    headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
  )
  if r.status_code == 429:
    raise SlackRetry(f"rate limited; Slack asks to wait {r.headers.get('Retry-After', '?')}s")
  if r.status_code >= 500:
    raise SlackRetry(f"HTTP {r.status_code}")
  data = r.json()
  if not data.get("ok"):
    error = data.get("error", "unknown_error")
    if error in PERMANENT_ERRORS:
      raise Permanent(f"slack {method}: {error}")
    raise SlackRetry(f"slack {method}: {error}")
  return data

def assignment_blocks(a: Assignment) -> list[dict[str, Any]]:
  value = json.dumps({"r": str(a.request_id), "s": a.step})
  inbox = f"{settings.public_url.rstrip('/')}/#/requests/{a.request_id}"
  return [
    {"type": "section", "text": {"type": "mrkdwn",
      "text": f"*Approval needed* · {a.risk_level.value} risk\n{esc(a.headline)}"}},
    {"type": "context", "elements": [{"type": "mrkdwn",
      "text": f"Why: {esc(a.rationale or 'no reason given')}  ·  "
              f"Decide by {a.due_at:%d %b %H:%M} UTC"}]},
    {"type": "actions", "block_id": "izin_decision", "elements": [
      {"type": "button", "action_id": "approve", "style": "primary",
       "text": {"type": "plain_text", "text": "Approve"}, "value": value},
      {"type": "button", "action_id": "deny", "style": "danger",
       "text": {"type": "plain_text", "text": "Deny…"}, "value": value},
      {"type": "button", "action_id": "open",
       "text": {"type": "plain_text", "text": "Open in inbox"}, "url": inbox},
    ]},
  ]

async def post_assignment(
  http: httpx.AsyncClient, request_id: uuid.UUID, principal_id: uuid.UUID, payload: dict[str, Any]
) -> None:
  a = await load_assignment(request_id, principal_id, payload["step"])
  if not a.slack_user_id:
    raise Permanent(f"{a.name} has no Slack account linked")
  data = await slack_call(http, "chat.postMessage", {
    "channel": a.slack_user_id,
    "text": f"Approval needed: {esc(a.headline)}",
    "blocks": assignment_blocks(a),
  })
  async with SessionLocal() as s:
    s.add(SlackMessage(
      request_id=request_id, principal_id=principal_id, step=a.step,
      channel=data["channel"], ts=data["ts"],
    ))
    await s.commit()

def describe(view: DecisionStatusOut) -> str:
  d = view.decision
  if d is None:
    return "Moved to the next approver; no longer waiting on you."
  label = {"approve": "Approved", "modify": "Approved with changes", "deny": "Denied"}[d.verdict.value]
  outcome = f"{label} by {d.principal_name}"
  return f"{outcome}: {d.comment}" if d.comment else f"{outcome}."

async def update_message(http: httpx.AsyncClient, payload: dict[str, Any]) -> None:
  async with SessionLocal() as s:
    msg = await s.get(SlackMessage, payload["message_id"])
    if msg is None:
      raise Permanent("slack message record not found")
    view = await load_view(s, msg.request_id)
    rendered = await s.scalar(select(Request.rendered).where(Request.id == msg.request_id))
  outcome = describe(view)  # from current state, not from the event: order-independent
  await slack_call(http, "chat.update", {
    "channel": msg.channel,
    "ts": msg.ts,
    "text": esc(outcome),
    "blocks": [
      {"type": "section", "text": {"type": "mrkdwn", "text": esc(rendered["headline"])}},
      {"type": "context", "elements": [{"type": "mrkdwn", "text": esc(outcome)}]},
    ],
  })