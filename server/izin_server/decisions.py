import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.db import SessionLocal
from izin_server.errors import AlreadyDecided, Forbidden, InvalidInput, NotFound
from izin_server.models import (
  Decision, Principal, Request, RequestEvent, RequestStatus, Route, Verdict
)
from izin_server.schemas import DecicionIn, DecisionOut, DecisionStatusOut

POLL_INTERVAL = 1.0

NEXT_STATUS = {Verdict.APPROVE: RequestStatus.APPROVED, Verdict.DENY: RequestStatus.DENIED}
PAST_TENSE = {Verdict.APPROVE: 'approved', Verdict.DENY: 'denied', Verdict.MODIFY: 'modified'}

async def decide(
  session: AsyncSession, request_id: uuid.UUID, body: DecicionIn
) -> DecisionStatusOut:
  if body.verdict not in NEXT_STATUS:
    raise InvalidInput("the modify verdict arrives in v0.2")

  route = await session.scalar(
    select(Route)
    .where(Route.request_id == request_id)
    .order_by(Route.step_index.desc())
    .limit(1)
  )
  if route is None:
    raise NotFound("request not found")

  principal = await session.get(Principal, body.principal_id)
  if principal is None:
    raise InvalidInput("unknown principal")
  if not _may_decide(principal, route):
    raise Forbidden(f"{principal.name} is not an approver for this request")

  rendered = await session.scalar(
    update(Request)
    .where(Request.id == request_id, Request.status == RequestStatus.PENDING)
    .values(status=NEXT_STATUS[body.verdict])
    .returning(Request.rendered)
    .execution_options(synchronize_session=False)
  )
  if rendered is None:
    raise _already_decided(await _load_view(session, request_id))

  await session.execute(
    insert(Decision).values(
      request_id=request_id,
      principal_id=principal.id,
      verdict=body.verdict,
      comment=body.comment,
      rendered_snapshot=rendered
    )
  )
  await session.execute(
    insert(RequestEvent).values(
      request_id=request_id,
      type="decided",
      payload={"verdict": body.verdict.value, "principal_id": str(principal.id)}
    )
  )
  await session.commit()

  return await _load_view(session, request_id)

def _may_decide(principal: Principal, route: Route) -> bool:
  return principal.id in route.principal_ids or (
    route.role is not None and route.role in principal.roles
  )

def _already_decided(view: DecisionStatusOut) -> AlreadyDecided:
  d = view.decision
  if d is None:
    return AlreadyDecided(f"This request is {view.status.value.lower()}, not pending.")
  return AlreadyDecided(f"Already {PAST_TENSE[d.verdict]} by {d.principal_name}")

async def _load_view(session: AsyncSession, request_id: uuid.UUID) -> DecisionStatusOut:
  row = (
    await session.execute(
      select(
        Request.id,
        Request.status,
        Request.args,
        Decision.verdict,
        Decision.comment,
        Decision.modified_args,
        Decision.decided_at,
        Principal.name.label("principal_name")
      )
      .outerjoin(Decision, Decision.request_id == Request.id)
      .outerjoin(Principal, Principal.id == Decision.principal_id)
      .where(Request.id == request_id)
    )
  ).one_or_none()
  if row is None:
    raise NotFound("request not found")

  decision = None
  if row.verdict is not None:
    decision = DecisionOut(
      verdict=row.verdict,
      comment=row.comment,
      principal_name=row.principal_name,
      decided_at=row.decided_at,
      args=row.modified_args if row.modified_args is not None else row.args
    )
  return DecisionStatusOut(request_id=row.id, status=row.status, decision=decision)

async def wait_for_decision(
  request_id: uuid.UUID,
  wait: float,
  is_disconnected: Callable[[], Awaitable[bool]]
) -> DecisionStatusOut:
  loop = asyncio.get_running_loop()
  deadline = loop.time() + wait
  while True:
    async with SessionLocal() as session:
      view = await _load_view(session, request_id)
    remaining = deadline - loop.time()
    if view.status != RequestStatus.PENDING or remaining <= 0 or await is_disconnected():
      return view
    await asyncio.sleep(min(POLL_INTERVAL, remaining))