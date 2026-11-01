import re
import uuid
from typing import Any

import httpx

from izin_server.assignments import Assignment, Permanent, load_assignment
from izin_server.config import settings
from izin_server.links import mint

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
PERMANENT_CODES = {100, 190, 131026, 131030, 131051, 132000, 132001, 132005, 132007, 132012, 133010}

class WhatsAppRetry(Exception):
  """A WhatsApp failure worth retrying."""

def _param(raw: str, limit: int = 60) -> str:
  """Template variables may not contain newlines, tabs or long runs of spaces."""
  return " ".join(raw.split())[:limit]

def template_message(a: Assignment, token: str) -> dict[str, Any]:
  return {
    "messaging_product": "whatsapp",
    "to": a.phone.lstrip("+"),
    "type": "template",
    "template": {
      "name": settings.whatsapp_template,
      "language": {"code": settings.whatsapp_template_lang},
      "components": [
        {"type": "body", "parameters": [
          {"type": "text", "text": _param(a.action)},
          {"type": "text", "text": a.risk_level.value},
          {"type": "text", "text": f"{a.due_at:%d %b %H:%M} UTC"},
        ]},
        {"type": "button", "sub_type": "url", "index": "0", "parameters": [
          {"type": "text", "text": token},
        ]},
      ],
    },
  }

async def send_whatsapp(
  http: httpx.AsyncClient, request_id: uuid.UUID, principal_id: uuid.UUID, payload: dict[str, Any]
) -> None:
  if not (settings.whatsapp_token and settings.whatsapp_phone_number_id):
    raise Permanent("WhatsApp is not configured")
  a = await load_assignment(request_id, principal_id, payload["step"])
  if not a.whatsapp_opted_in:
    raise Permanent(f"{a.name} has not opted in to WhatsApp")
  if not a.phone or not E164.match(a.phone):
    raise Permanent(f"{a.name} has no valid E.164 phone number")

  token = mint(request_id, principal_id, a.step, int(a.due_at.timestamp()))
  r = await http.post(
    f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}/messages",
    json=template_message(a, token),
    headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
  )
  if r.is_success:
    return
  try:
    error = r.json().get("error", {})
  except ValueError:
    error = {}
  code = error.get("code")
  message = f"whatsapp {code}: {error.get('message', r.text[:200])}"
  if code in PERMANENT_CODES:
    raise Permanent(message)
  raise WhatsAppRetry(message)