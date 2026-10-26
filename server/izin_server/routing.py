import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.decisions import write_decision
from izin_server.models import Action, Principal, RequestEvent, Route, Verdict
from izin_server.system import ROUTING_PRINCIPAL_ID


def build_plan(action: Action) -> dict[str, Any]:
  steps = action.steps or [{
    "role": settings.default_approver,
    "principal_ids": [],
    "after": int(action.default_timeout.total_seconds()),
  }]
  return {"on_timeout": action.on_timeout.value, "steps": steps}

def total_wait(plan: dict[str, Any]) -> timedelta:
  return timedelta(seconds=sum(step["after"] for step in plan["steps"]))

# async def activate_step(
#   session: AsyncSession, request_id: uuid.UUID, plan: dict[str, Any], index: str
# ) -> int:
#   step = plan["steps"][index]
#   await session.execute(
#     insert(Route).values(
#       request_id=request_id,
#       step_index=index,
#       role=step["role"],
#       principal_ids=[uuid.UUID(p) for p in step["principal_ids"]],
#       due_at=func.now() + timedelta(seconds=step["after"])
#     )
#   )

#   return index
async def _candidate(session: AsyncSession, step: dict[str, Any]) -> list[Principal]:
  targets = []
  if step["role"]:
    targets.append(Principal.roles.contains([step["role"]]))
  if step["principal_ids"]:
    targets.append(Principal.id.in_([uuid.UUID(p) for p in step["principal_ids"]]))
  return list(
    await session.scalars(select(Principal).where(or_(*targets)).order_by(Principal.name))
  )

async def route_to(
  session: AsyncSession, request_id: uuid.UUID, plan: dict[str, Any], start: int
) -> int | None:
  for index in range(start, len(plan["steps"])):
    step = plan["steps"][index]
    people = await _candidate(session, step)
    if any(p.available for p in people):
      await session.execute(
        insert(Route).values(
          request_id=request_id,
          step_index=index,
          role=step["role"],
          principal_ids=[uuid.UUID(p) for p in step["principal_ids"]],
          due_at=func.now() + timedelta(seconds=step["after"])
        )
      )
      return index
    await session.execute(
      insert(RequestEvent).values(
        request_id=request_id,
        type="skipped",
        payload={
          "step": index,
          "role": step["role"],
          "principal_ids": step["principal_ids"],
          "unavailable": [{"id": str(p.id), "name": p.name} for p in people] 
        }
      )
    )
  return None

async def close_unroutable(session: AsyncSession, request_id: uuid.UUID) -> None:
  written = await write_decision(
    session, request_id,
    principal_id=ROUTING_PRINCIPAL_ID, verdict=Verdict.DENY,
    comment="Denied automatically: no approver was available.",
    event={"reason": "no_available_approver"},
  )
  if not written:
    raise RuntimeError(f"request {request_id}: not PENDING while closing it as unroutable")