import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.decisions import authorize, load_view
from izin_server.models import (
  Action,
  Decision,
  Principal,
  Request,
  RequestStatus,
  Route,
  Verdict,
)
from izin_server.schemas import InboxDetail, InboxItem, RenderedScreen, EditableOut


async def list_inbox(session: AsyncSession, principal: Principal) -> list[InboxItem]:
  current = (
    select(Route.request_id, func.max(Route.step_index).label("step"))
    .group_by(Route.request_id)
    .subquery()
  )
  rows = (
    await session.execute(
      select(
        Request.id, Request.rendered, Request.agent_name,
        Request.created_at, Request.expires_at,
        Action.display_name, Action.risk_level,
        Route.due_at
      )
      .join(Action, Action.id == Request.action_id)
      .join(current, current.c.request_id == Request.id)
      .join(Route, (Route.request_id == Request.id) & (Route.step_index == current.c.step))
      .where(Request.status == RequestStatus.PENDING)
      .where(or_(Route.principal_ids.contains([principal.id]), Route.role.in_(principal.roles)))
      .order_by(Route.due_at.asc(), Request.created_at.asc())
      .limit(100)
    )
  ).all()

  return [
    InboxItem(
      id=r.id,
      action_display_name=r.display_name,
      risk_level=r.risk_level,
      headline=r.rendered["headline"],
      agent_name=r.agent_name,
      created_at=r.created_at,
      expires_at=r.expires_at,
      due_at=r.due_at
    )
    for r in rows
  ]

async def load_detail(
  session: AsyncSession, request_id: uuid.UUID, principal: Principal
) -> InboxDetail:
  await authorize(session, request_id, principal)

  request, action = (
    await session.execute(
      select(Request, Action)
      .join(Action, Action.id == Request.action_id)
      .where(Request.id == request_id)
      .execution_options(populate_existing=True)
    )
  ).one()
  view = await load_view(session, request_id)

  similar = await session.scalar(
    select(func.count())
    .select_from(Decision)
    .join(Request, Request.id == Decision.request_id)
    .where(
      Request.action_id == action.id,
      Decision.verdict.in_([Verdict.APPROVE, Verdict.MODIFY]),
      Decision.decided_at > func.now() - timedelta(days=7)
    )
  )

  spec = request.screen_spec
  props = spec["args_schema"].get("properties", {})
  editable = [
    EditableOut(
      field=e["field"], type=props[e["field"]]["type"],
      only=e.get("only"), value=request.args.get(e["field"])
    )
    for e in spec["editable"]
  ]

  return InboxDetail(
    id=request.id,
    status=request.status,
    action_display_name=action.display_name,
    risk_level=action.risk_level,
    agent_name=request.agent_name,
    rationale=request.rationale,
    rendered=RenderedScreen(**request.rendered),
    context=request.context,
    args=request.args,
    created_at=request.created_at,
    expires_at=request.expires_at,
    similar_approved_7d=similar,
    decision=view.decision,
    editable=editable
  )