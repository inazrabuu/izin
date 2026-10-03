import uuid
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.errors import IdempotencyConflict, InvalidInput
from izin_server.models import Action, Outbox, Request, RequestEvent, Route
from izin_server.rendering import RenderError, render_screen
from izin_server.schemas import RequestIn

async def create_request(session: AsyncSession, body: RequestIn) -> tuple[Request, bool]:
  action = await session.scalar(select(Action).where(Action.name == body.action))
  if action is None:
    raise InvalidInput(f"unknown action {body.action!r}")

  existing = await _find(session, body.idempotency_key)
  if existing is not None:
    _ensure_same_request(existing, action, body)
    return existing, False

  _validate_args(action.args_schema, body.args)
  try:
    rendered = render_screen(
      headline=action.render_template,
      blast_radius=action.blast_radius,
      if_denied=action.if_denied,
      args=body.args
    )
  except RenderError as e:
    raise InvalidInput(f"could not render request: {e}") from e

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
      expires_at=func.now() + action.default_timeout
    )
    .on_conflict_do_nothing(index_elements=[Request.idempotency_key])
    .returning(Request.id)
  )

  if new_id is None:
    existing = await _find(session, body.idempotency_key)
    _ensure_same_request(existing, action, body)
    return existing, False

  await session.execute(
    insert(Route).values(
      request_id=new_id, step_index=0, role=settings.default_approver
    )
  )
  await session.execute(
    insert(RequestEvent).values(
      request_id=new_id,
      type="created",
      payload=_outbox_payload(new_id, action, rendered)
    )
  )
  await session.commit()

  return await session.get(Request, new_id), True

async def _find(session: AsyncSession, key: str) -> Request:
  return await session.scalar(select(Request).where(Request.idempotency_key == key))

async def _ensure_same_request(existing: Request, action: Action, body: RequestIn) -> None:
  same = (
    existing.action_id == action.id
    and existing.agent_run_id == body.agent_run_id
    and existing.args == body.args
  )

  if not same:
    raise IdempotencyConflict(
      "idempotency_key was already used for a different request (action, agent_run_id or args differ)"
    )

def _validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
  error = best_match(Draft202012Validator(schema).iter_errors(args))
  if error is not None:
    where = "/".join(map(str, error.path)) or "(root)"
    raise InvalidInput(f"args invalid at {where} : {error.message}")

def _outbox_payload(request_id: uuid.UUID, action: Action, rendered: dict) -> dict:
  return {
    "event": "request.created",
    "request_id": str(request_id),
    "action": action.name,
    "risk_level": action.risk_level,
    "headline": rendered["headline"]
  }