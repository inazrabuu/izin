import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.models import Action, Route

def build_plan(action: Action) -> dict[str, Any]:
  steps = action.steps or [{
    "role": settings.default_approver,
    "principal_ids": [],
    "after": int(action.default_timeout.total_seconds()),
  }]
  return {"on_timeout": action.on_timeout.value, "steps": steps}

def total_wait(plan: dict[str, Any]) -> timedelta:
  return timedelta(seconds=sum(step["after"] for step in plan["steps"]))

async def activate_step(
  session: AsyncSession, request_id: uuid.UUID, plan: dict[str, Any], index: str
) -> int:
  step = plan["steps"][index]
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