import uuid
from email.message import EmailMessage
from typing import Any

import aiosmtplib

from izin_server.assignments import Permanent, load_assignment
from izin_server.config import settings
from izin_server.links import mint


async def compose(request_id: uuid.UUID, principal_id: uuid.UUID, step: int) -> EmailMessage:
  a = await load_assignment(request_id, principal_id, step)
  if not a.email:
    raise Permanent(f"{a.name} has no email address")

  token = mint(request_id, principal_id, step, int(a.due_at.timestamp()))
  link = f"{settings.public_url.rstrip("/")}/#/link/{token}"

  msg = EmailMessage()
  msg["From"] = settings.smtp_from
  msg["To"] = a.email
  msg["Subject"] = f"Approval needed: {a.action}"
  msg.set_content(
    f"{a.headline}\n\n"
    f"Why: {a.rationale or '(no reason given)'}\n"
    f"Risk: {a.risk_level.value}\n"
    f"Decide by: {a.due_at:%d %b %Y %H:%M} UTC\n\n"
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