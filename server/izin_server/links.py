import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.decisions import decide
from izin_server.errors import Forbidden, Unauthorized
from izin_server.inbox import load_detail
from izin_server.models import Action, Principal, Request, RiskLevel
from izin_server.schemas import InboxDecisionIn, InboxDetail

LINK_DECIDES = {RiskLevel.LOW, RiskLevel.MEDIUM}

@dataclass(frozen=True)
class LinkClaims:
  request_id: uuid.UUID
  principal_id: uuid.UUID
  step: int
  expires_at: int

def _b64(raw: bytes) -> str:
  return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

def _unb64(text: str) -> str:
  return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

def _sign(body: str) -> str:
  return _b64(hmac.new(settings.link_secret.encode(), body.encode(), hashlib.sha256).digest())

def mint(request_id: uuid.UUID, principal_id: uuid.UUID, step: int, expires_at: int) -> str:
  claims = {"r": str(request_id), "p": str(principal_id), "s": step, "e": expires_at}
  body = _b64(json.dumps(claims, separators=(",", ":")).encode())
  return f"{body}.{_sign(body)}"

def verify(token: str, now: float | None = None) -> LinkClaims:
  try:
    body, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(body)):
      raise ValueError("bad signature")
    data = json.loads(_unb64(body))
    claims = LinkClaims(uuid.UUID(data["r"]), uuid.UUID(data["p"]), int(data["s"]), int(data["e"]))
  except (ValueError, KeyError, TypeError) as e:
    raise Unauthorized("this link is not valid") from e
  if (time.time() if now is None else now) >= claims.expires_at:
    raise Unauthorized("this link has expired")
  return claims

async def open_link(session: AsyncSession, token: str | None) -> tuple[LinkClaims, Principal]:
  if not token:
    raise Unauthorized("missing link")
  claims = verify(token)
  principal = await session.get(Principal, claims.principal_id)
  if principal is None:
    raise Unauthorized("this link is not valid")
  return claims, principal

async def _risk(session: AsyncSession, request_id: uuid.UUID) -> RiskLevel:
  return await session.scalar(
    select(Action.risk_level)
    .join(Request, Request.action_id == Action.id)
    .where(Request.id == request_id)
  )

async def link_view(session: AsyncSession, claims: LinkClaims, principal: Principal) -> InboxDetail:
  detail = await load_detail(session, claims.request_id, principal)
  detail.link_can_decide = await _risk(session, claims.request_id) in LINK_DECIDES
  return detail

async def decide_by_link(
  session: AsyncSession, claims: LinkClaims, principal: Principal, body: InboxDecisionIn
) -> None:
  if await _risk(session, claims.request_id) not in LINK_DECIDES:
    raise Forbidden("Sign in to the inbox to decide high-risk requests.")
  await decide(session, claims.request_id, principal, body, step=claims.step, via="link")