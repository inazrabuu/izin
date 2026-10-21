import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import insert, select, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.db import SessionLocal
from izin_server.errors import AlreadyDecided, Forbidden, InvalidInput, NotFound, NotDecided
from izin_server.models import (
  Decision, Principal, Request, RequestEvent, RequestStatus, Route, Verdict
)
from izin_server.schemas import InboxDecisionIn, DecisionOut, DecisionStatusOut

POLL_INTERVAL = 1.0

NEXT_STATUS = {Verdict.APPROVE: RequestStatus.APPROVED, Verdict.DENY: RequestStatus.DENIED}
PAST_TENSE = {Verdict.APPROVE: 'approved', Verdict.DENY: 'denied', Verdict.MODIFY: 'modified'}

async def latest_route(session: AsyncSession, request_id: uuid.UUID) -> Route | None:
  return await session.scalar(
    select(Route)
    .where(Route.request_id == request_id)
    .order_by(Route.step_index.desc())
    .limit(1)
  )

async def authorize(session: AsyncSession, request_id: uuid.UUID, principal: Principal) -> None:
  route = await latest_route(session, request_id)
  if route is None:
    raise NotFound("request not found")
  if not may_decide(principal, route):
    raise Forbidden(f"{principal.name} is not an approver for this request")

async def write_decision(
  session: AsyncSession,
  request_id: uuid.UUID,
  *,
  principal_id: uuid.UUID,
  verdict: Verdict,
  comment: str | None,
  from_status: RequestStatus = RequestStatus.PENDING,
  only_if: tuple = (),
  event: dict[str, Any] | None = None
) -> bool:
  rendered = await session.scalar(
    update(Request)
    .where(Request.id == request_id, Request.status == from_status, *only_if)
    .values(status=NEXT_STATUS[verdict])
    .returning(Request.rendered)
    .execution_options(synchronize_session=False)
  )
  if rendered is None:
    return False

  await session.execute(
    insert(RequestEvent).values(
      request_id=request_id,
      type="decided",
      payload={"verdict": verdict.value, "principal_id": str(principal_id), **(event or {})}
    )
  )

  return True

NOT_EXPIRED = or_(Request.expires_at.is_(None), Request.expires_at > func.now())

async def decide(
  session: AsyncSession, request_id: uuid.UUID, principal: Principal, body: InboxDecisionIn
) -> None:
  if body.verdict not in NEXT_STATUS:
    raise InvalidInput("the modify verdict arrives in v0.2")
  await authorize(session, request_id, principal)

  written = await write_decision(
    session, request_id,
    principal_id=principal.id, verdict=body.verdict, comment=body.comment,
    only_if=(NOT_EXPIRED,),
  )
  if not written:
    await session.rollback()
    raise _already_decide(await load_view(session, request_id))
  await session.commit()

def _already_decide(view: DecisionStatusOut) -> AlreadyDecided:
  d = view.decision
  if d is not None:
    return AlreadyDecided(f"Already {PAST_TENSE[d.verdict]} by {Principal.name} ")
  if view.status == RequestStatus.PENDING:
    return AlreadyDecided("This request has expired and is being closed.")
  return AlreadyDecided(f"This request is {view.status.value.lower()}, not pending.")

def may_decide(principal: Principal, route: Route) -> bool:
  return principal.id in route.principal_ids or (
    route.role is not None and route.role in principal.roles
  )

def _already_decided(view: DecisionStatusOut) -> AlreadyDecided:
  d = view.decision
  if d is None:
    return AlreadyDecided(f"This request is {view.status.value.lower()}, not pending.")
  return AlreadyDecided(f"Already {PAST_TENSE[d.verdict]} by {d.principal_name}")

async def load_view(session: AsyncSession, request_id: uuid.UUID) -> DecisionStatusOut:
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
      view = await load_view(session, request_id)
    remaining = deadline - loop.time()
    if view.status != RequestStatus.PENDING or remaining <= 0 or await is_disconnected():
      return view
    await asyncio.sleep(min(POLL_INTERVAL, remaining))

CONSUMABLE = (RequestStatus.APPROVED, RequestStatus.DENIED)

async def consume(session: AsyncSession, request_id: uuid.UUID) -> DecisionStatusOut:
  consumed = await session.scalar(
    update(Request)
    .where(Request.id == request_id, Request.status.in_(CONSUMABLE))
    .values(status=RequestStatus.CONSUMED)
    .returning(Request.id)
    .execution_options(synchronize_session=False)
  )
  if consumed is None:
    view = await load_view(session, request_id)
    if view.status == RequestStatus.CONSUMED:
      return view
    raise NotDecided(
      f"This request is {view.status.value.lower()}; there is no decision to consume"
    )

  await session.execute(insert(RequestEvent).values(request_id=request_id, type="consumed"))
  await session.commit()

  return await load_view(session, request_id)

