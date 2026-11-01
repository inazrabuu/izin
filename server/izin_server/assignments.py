import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from izin_server.db import SessionLocal
from izin_server.models import (
  Action,
  Principal,
  Request,
  RequestStatus,
  RiskLevel,
  Route,
)


class Stale(Exception):
  """The request moved on before we sent anything; there is nothing to tell."""

class Permanent(Exception):
  """Retrying will not help."""

@dataclass(frozen=True)
class Assignment:
  request_id: uuid.UUID
  principal_id: uuid.UUID
  step: int
  headline: str
  rationale: str | None
  action: str
  risk_level: RiskLevel
  name: str
  email: str | None
  slack_user_id: str | None
  due_at: datetime
  phone: str | None
  whatsapp_opted_in: bool

async def load_assignment(request_id: uuid.UUID, principal_id: uuid.UUID, step: int) -> Assignment:
  async with SessionLocal() as s:
    row = (
      await s.execute(
        select(
          Request.status, Request.rendered, Request.rationale,
          Action.display_name, Action.risk_level,
          Principal.name, Principal.email, Principal.slack_user_id, Route.due_at,
          Principal.phone, Principal.whatsapp_opted_in_at
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
  return Assignment(
    request_id=request_id, principal_id=principal_id, step=step,
    headline=row.rendered["headline"], rationale=row.rationale,
    action=row.display_name, risk_level=row.risk_level,
    name=row.name, email=row.email, slack_user_id=row.slack_user_id, due_at=row.due_at,
    phone=row.phone, whatsapp_opted_in=row.whatsapp_opted_in_at is not None,
  )