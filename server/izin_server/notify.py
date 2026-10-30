import uuid
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.errors import InvalidInput, NotFound
from izin_server.models import (
  Action,
  Outbox,
  OutboxStatus,
  Principal,
  Request,
  RequestEvent,
)

MAX_ATTEMPTS = 8

WEBHOOK = "webhook"

async def enqueue(
  session: AsyncSession,
  request_id: uuid.UUID,
  *,
  event: str,
  channel: str,
  key: str,
  payload: dict[str, Any],
  principal_id: uuid.UUID | None = None
) -> None:
  await session.execute(
    pg_insert(Outbox)
    .values(
      request_id=request_id,
      event=event,
      channel=channel,
      principal_id=principal_id,
      payload=payload,
      dedupe_key=f"{request_id}:{event}:{channel}:{key}",
      give_up_after=MAX_ATTEMPTS
    )
    .on_conflict_do_nothing(index_elements=[Outbox.dedupe_key])
  )

async def emit(
  session: AsyncSession, request_id: uuid.UUID, event: str, *, key: str = "-", **extra: Any
) -> None:
  row = (
    await session.execute(
      select(Action.name, Action.risk_level, Request.agent_run_id, func.now().label("at"))
      .join(Request, Request.action_id == Action.id)
      .where(Request.id == request_id)
    )
  ).one()
  payload = {
    "event": event,
    "request_id": str(request_id),
    "agent_run_id": row.agent_run_id,
    "action": row.name,
    "risk_level": row.risk_level.value,
    "at": row.at.isoformat(),
    **extra
  }
  await enqueue(session, request_id, event=event, channel=WEBHOOK, key=key, payload=payload)

async def assign(
  session: AsyncSession, request_id: uuid.UUID, step: int, people: list[Principal]
) -> None:
  for channel in settings.human_channels:
    for person in people:
      if person.available:
        await enqueue(
          session, request_id,
          event="request.assigned", channel=channel,
          key=f"{step}:{person.id}", principal_id=person.id,
          payload={"event": "request.assigned", "request_id": str(request_id), "step": step}
        )

async def list_outbox(session: AsyncSession, status: OutboxStatus, limit: int) -> list[Outbox]:
  return list(
    await session.scalars(
      select(Outbox).where(Outbox.status == status).order_by(Outbox.id.desc()).limit(limit)
    )
  )

async def retry_delivery(session: AsyncSession, outbox_id: int) -> Outbox:
  previous_error = await session.scalar(select(Outbox.last_error).where(Outbox.id == outbox_id))
  row = await session.scalar(
    update(Outbox)
    .where(Outbox.id == outbox_id, Outbox.status == OutboxStatus.FAILED)
    .values(
      status=OutboxStatus.PENDING,
      next_attempt_at=func.now(),
      give_up_after=Outbox.attempts + MAX_ATTEMPTS,
    )
    .returning(Outbox)
    .execution_options(populate_existing=True)
  )
  if row is None:
    exists = await session.scalar(select(Outbox.id).where(Outbox.id == outbox_id))
    if exists is None:
      raise NotFound("delivery not found")
    raise InvalidInput("only failed deliveries can be retried")

  await session.execute(
    insert(RequestEvent).values(
      request_id=row.request_id,
      type="notify_retried",
      payload={
        "delivery_id": str(row.id),
        "channel": row.channel,
        "previous_error": previous_error,
        "by": "admin",
      },
    )
  )
  await session.commit()
  return row