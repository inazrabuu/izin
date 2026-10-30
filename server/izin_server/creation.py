
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.errors import IdempotencyConflict, InvalidInput
from izin_server.models import Action, Request, RequestEvent
from izin_server.modify import render_screen, screen_spec, validate_args
from izin_server.notify import emit
from izin_server.rendering import RenderError
from izin_server.routing import build_plan, close_unroutable, route_to, total_wait
from izin_server.schemas import RequestIn


async def create_request(session: AsyncSession, body: RequestIn) -> tuple[Request, bool]:
  action = await session.scalar(select(Action).where(Action.name == body.action))
  if action is None:
    raise InvalidInput(f"unknown action {body.action!r}")

  existing = await _find(session, body.idempotency_key)
  if existing is not None:
    _ensure_same_request(existing, action, body)
    return existing, False

  validate_args(action.args_schema, body.args)
  spec = screen_spec(action)
  try:
    rendered = render_screen(
      headline=action.render_template,
      blast_radius=action.blast_radius,
      if_denied=action.if_denied,
      args=body.args
    )
  except RenderError as e:
    raise InvalidInput(f"could not render request: {e}") from e

  plan = build_plan(action)

  new_id = await session.scalar(
    pg_insert(Request)
    .values(
      action_id=action.id,
      idempotency_key=body.idempotency_key,
      agent_run_id=body.agent_run_id,
      agent_name=body.agent_name,
      args=body.args,
      rationale=body.rationale,
      context=body.context,
      rendered=rendered,
      plan=plan,
      screen_spec=spec,
      expires_at=func.now() + total_wait(plan)
    )
    .on_conflict_do_nothing(index_elements=[Request.idempotency_key])
    .returning(Request.id)
  )

  if new_id is None:
    existing = await _find(session, body.idempotency_key)
    _ensure_same_request(existing, action, body)
    return existing, False

  await session.execute(
    insert(RequestEvent).values(
      request_id=new_id,
      type="created",
      payload={"agent_run_id": body.agent_run_id, "agent_name": body.agent_name},
    )
  )

  await emit(session, new_id, "request.created")

  if await route_to(session, new_id, plan, 0) is None:
    await close_unroutable(session, new_id)

  await session.commit()
    
  return await session.get(Request, new_id), True

async def _find(session: AsyncSession, key: str) -> Request:
  return await session.scalar(select(Request).where(Request.idempotency_key == key))

def _ensure_same_request(existing: Request, action: Action, body: RequestIn) -> None:
  same = (
    existing.action_id == action.id
    and existing.agent_run_id == body.agent_run_id
    and existing.args == body.args
  )

  if not same:
    raise IdempotencyConflict(
      "idempotency_key was already used for a different request (action, agent_run_id or args differ)"
    )