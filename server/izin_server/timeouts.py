import logging
import uuid
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from izin_server.db import SessionLocal
from izin_server.decisions import lock_current_route, write_decision
from izin_server.models import OnTimeout, Request, RequestEvent, RequestStatus, Route, Verdict
from izin_server.routing import route_to, close_unroutable
from izin_server.system import TIMEOUT_PRINCIPAL_ID

log = logging.getLogger("izin.timeouts")

Outcome = Verdict | Literal["escalated"]

def timeout_outcome(on_timeout: OnTimeout, waited: timedelta) -> tuple[Verdict, str]:
  after = f"no decision within {_duration(waited)}"
  if on_timeout == OnTimeout.APPROVE:
    return Verdict.APPROVE, f"Approved automatically: {after}."
  return Verdict.DENY, f"Denied automatically: {after}."

def _duration(td: timedelta) -> str:
  hours, minutes = divmod(max(0, round(td.total_seconds() / 60)), 60)
  if hours and minutes:
    return f"{hours}h {minutes}m"
  return f"{hours}h" if hours else f"{minutes}m"

async def advance_one(request_id: uuid.UUID) -> Outcome | None:
  """Move one overdue request forward: to its next step, or to its on_timeout outcome."""
  async with SessionLocal() as session:
    current = await lock_current_route(session, request_id)
    if current is None:
      return None  # already decided
    route, is_open = current
    if is_open:
      return None  # not due, or another sweeper escalated it while we waited for the lock

    plan = await session.scalar(select(Request.plan).where(Request.id == request_id))
    if route.step_index + 1 < len(plan["steps"]):
      outcome = await _escalate(session, request_id, plan, route.step_index)
    else:
      outcome = await _expire(session, request_id, plan)
    await session.commit()
    return outcome

async def _move(
  session: AsyncSession, request_id: uuid.UUID, frm: RequestStatus, to: RequestStatus
) -> None:
  moved = await session.scalar(
    update(Request)
    .where(Request.id == request_id, Request.status == frm)
    .values(status=to)
    .returning(Request.id)
    .execution_options(synchronize_session=False)
  )
  if moved is None:
    raise RuntimeError(f"request {request_id}: expected {frm} under our own row lock")

async def _escalate(
  session: AsyncSession, request_id: uuid.UUID, plan: dict[str, Any], from_step: int
) -> Outcome:
  to_step = await route_to(session, request_id, plan, from_step + 1)
  if to_step is None:
    await close_unroutable(session, request_id)
    return Verdict.DENY

  await _move(session, request_id, RequestStatus.PENDING, RequestStatus.ESCALATED)
  await _move(session, request_id, RequestStatus.ESCALATED, RequestStatus.PENDING)
  await session.execute(
    insert(RequestEvent).values(
      request_id=request_id, type="escalated",
      payload={"from_step": from_step, "to_step": to_step}
    )
  )
  return "escalated"

async def _expire(session: AsyncSession, request_id: uuid.UUID, plan: dict[str, Any]) -> Verdict:
  await _move(session, request_id, RequestStatus.PENDING, RequestStatus.EXPIRED)
  on_timeout = OnTimeout(plan["on_timeout"])
  waited = await session.scalar(
    select(func.now() - Request.created_at).where(Request.id == request_id)
  )
  verdict, comment = timeout_outcome(on_timeout, waited)

  await session.execute(
    insert(RequestEvent).values(
      request_id=request_id, type="expired", payload={"on_timeout": on_timeout.value}
    )
  )
  written = await write_decision(
    session, request_id,
    principal_id=TIMEOUT_PRINCIPAL_ID, verdict=verdict, comment=comment,
    from_status=RequestStatus.EXPIRED, event={"reason": "timeout"},
  )
  if not written:
    raise RuntimeError(f"request {request_id}: EXPIRED row vanished inside its own transaction")
  return verdict

async def due_requests(session: AsyncSession, limit: int) -> list[uuid.UUID]:
  later = aliased(Route)
  return list(
    await session.scalars(
      select(Route.request_id)
      .join(Request, Request.id == Route.request_id)
      .where(
        Request.status == RequestStatus.PENDING,
        Route.due_at <= func.now(),
        ~exists().where(later.request_id == Route.request_id, later.step_index > Route.step_index),
      )
      .order_by(Route.due_at)
      .limit(limit)
    )
  )

async def sweep_once(batch_size: int = 50) -> int:
  async with SessionLocal() as session:
    due = await due_requests(session, batch_size)

  moved = 0
  for request_id in due:
    try:
      if await advance_one(request_id) is not None:
        moved += 1
    except Exception:
      log.exception("timeout sweep failed for request %s", request_id)
  return moved