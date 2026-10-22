import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, insert, select, update

from izin_server.db import SessionLocal
from izin_server.decisions import write_decision
from izin_server.models import Action, OnTimeout, Request, RequestEvent, RequestStatus, Verdict
from izin_server.system import TIMEOUT_PRINCIPAL_ID

log = logging.getLogger("izin.timeouts")

def timeout_outcome(on_timeout: OnTimeout, waited: timedelta) -> tuple[Verdict, str]:
  after = f"no decision within {_duration(waited)}"
  match on_timeout:
    case OnTimeout.APPROVE:
      return Verdict.APPROVE, f"Approved automatically: {after}"
    case OnTimeout.ESCALATE:
      return Verdict.DENY, f"Denied automatically: {after} and no escalation step is configured"
    case _:
      return Verdict.DENY, f"Denied automatically: {after}"

def _duration(td: timedelta):
  hours, minutes = divmod(max(0, round(td.total_seconds() / 60)), 60)
  if hours and minutes:
    return f"{hours}h {minutes}m"
  return f"{hours}h" if hours else f"{minutes}m"

async def expire_one(request_id: uuid.UUID) -> Verdict | None:
  async with SessionLocal() as session:
    expired = await session.scalar(
      update(Request)
      .where(
        Request.id == request_id,
        Request.status == RequestStatus.PENDING,
        Request.expires_at <= func.now()
      )
      .values(status=RequestStatus.EXPIRED)
      .returning(Request.id)
      .execution_options(synchronize_session=False)
    )
    if expired is None:
      return None

    row = (
      await session.execute(
        select(Action.on_timeout, Request.created_at, Request.expires_at)
        .join(Action, Action.id == Request.action_id)
        .where(Request.id == request_id)
      )
    ).one()
    verdict, comment = timeout_outcome(row.on_timeout, row.expires_at - row.created_at)

    await session.execute(
      insert(RequestEvent).values(
        request_id=request_id, type="expired", payload={"on_timeout": row.on_timeout.value}
      )
    )
    written = await write_decision(
      session, request_id,
      principal_id=TIMEOUT_PRINCIPAL_ID, verdict=verdict, comment=comment,
      from_status=RequestStatus.EXPIRED, event={"reason": "timeout"}
    )
    if not written:
      raise RuntimeError(f"request {request_id}: EXPIRED row vanished inside its own transaction.")
    await session.commit()
    return verdict

async def sweep_once(batch_size: int = 50) -> int:
  async with SessionLocal() as session:
    due = list(
      await session.scalars(
        select(Request.id)
        .where(Request.status == RequestStatus.PENDING, Request.expires_at <= func.now())
        .order_by(Request.expires_at)
        .limit(batch_size)
      )
    )

  closed = 0
  for request_id in due:
    try:
      if await expire_one(request_id) is not None:
        closed += 1
    except Exception:
      log.exception("timeout sweep failed for request %s", request_id)
  return closed 