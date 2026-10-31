import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import aiosmtplib
from sqlalchemy import func, select

from izin_server.config import settings
from izin_server.db import SessionLocal
from izin_server.links import mint
from izin_server.models import Action, Principal, Request, RequestStatus, Route


class Stale(Exception):
  """The request moved on before we sent anything; there is nothing to tell."""

class Permanent(Exception):
  """Retrying will not help."""

async def compose(request_id: uuid.UUID, principal_id: uuid.UUID, step: int) -> EmailMessage:
  async with SessionLocal() as s:
    row = (
      await s.execute(
        select(
          Request.status, Request.rendered, Request.rationale,
          Action.display_name, Action.risk_level,
          Principal.name, Principal.email, Route.due_at
        )
        .join(Action, Action.id == Request.action_id)
        .join(Route, (Route.request_id == Request.id) & (Route.step_index == step))
        .join(Principal, Principal.id == principal_id)
        .where(Request.id == request_id)
      )
    ).one_or_none()
    latest = await s.scalar(
      select(func.max(Route.step_index)).where(Route.request_id == request_id)
    )

  if row is None or row.status != RequestStatus.PENDING or latest != step:
    raise Stale("request already decided or moved to another step")
  if row.due_at <= datetime.now(UTC):
    raise Stale("step deadline already passed")
  if not row.email:
    raise Permanent(f"{row.name} has no email address")

  token = mint(request_id, principal_id, step, int(row.due_at.timestamp()))
  link = f"{settings.public_url.rstrip("/")}/#/link/{token}"

  msg = EmailMessage()
  msg["From"] = settings.smtp_from
  msg["To"] = row.email
  msg["Subject"] = f"Approval needed: {row.display_name}"
  msg.set_content(
    f"{row.rendered['headline']}\n\n"
    f"Why: {row.rationale or '(no reason given)'}\n"
    f"Risk: {row.risk_level.value}\n"
    f"Decide by: {row.due_at:%d %b %Y %H:%M} UTC\n\n"
    f"Open the request:\n{link}\n\n"
    "This link is personal to you and stops working when your step ends. "
    "Please don't forward it.\n"
  )
  return msg

async def send_assignment(
    request_id: uuid.UUID, principal_id: uuid.UUID, payload: dict[str, Any]
) -> None:
  if not settings.smtp_host:
    raise Permanent("smtp_host is not configured")
  msg = await compose(request_id, principal_id, payload["step"])
  try:
    await aiosmtplib.send(
      msg,
      hostname=settings.smtp_host,
      port=settings.smtp_port,
      username=settings.smtp_username,
      password=settings.smtp_password,
      start_tls=settings.smtp_starttls,
      timeout=15
    )
  except aiosmtplib.SMTPRecipientRefused as e:
    if all(500 <= r.code < 600 for r in e.recipients):
      raise Permanent(f"recipient refused: {e.recipients[0].message}") from e
    raise
  except aiosmtplib.SMTPResponseException as e:
    if 500 <= e.code < 600:
      raise Permanent(f"SMTP {e.code}: {e.message}") from e
    raise