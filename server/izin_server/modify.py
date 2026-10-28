import uuid
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.errors import InvalidInput
from izin_server.models import Action, Request
from izin_server.rendering import RenderError, render_screen


def screen_spec(action: Action) -> dict[str, Any]:
  """Everything needed to render or edit this request's screen, frozen at creation."""
  return {
    "headline": action.render_template,
    "blast_radius": action.blast_radius,
    "if_denied": action.if_denied,
    "args_schema": action.args_schema,
    "editable": action.editable,
  }

def render_spec(spec: dict[str, Any], args: dict[str, Any]) -> dict[str, str | None]:
  return render_screen(
    headline=spec["headline"],
    blast_radius=spec["blast_radius"],
    if_denied=spec["if_denied"],
    args=args,
  )

def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
  error = best_match(Draft202012Validator(schema).iter_errors(args))
  if error is not None:
    where = "/".join(map(str, error.path)) or "(root)"
    raise InvalidInput(f"args invalid at {where}: {error.message}")

def apply_changes(
  spec: dict[str, Any], args: dict[str, Any], changes: dict[str, Any]
) -> dict[str, Any]:
  rules = {e["field"]: e.get("only") for e in spec["editable"]}
  if not rules:
    raise InvalidInput("this action does not allow changes")
  for field in changes:
    if field not in rules:
      raise InvalidInput(f"{field!r} cannot be changed for this action")

  modified = {**args, **changes}
  validate_args(spec["args_schema"], modified)  # types are known-good from here on

  for field, value in changes.items():
    before, only = args[field], rules[field]
    if value == before:
      raise InvalidInput(f"{field!r} is unchanged; approve instead")
    if only == "decrease" and not value < before:
      raise InvalidInput(f"{field!r} may only be lowered")
    if only == "increase" and not value > before:
      raise InvalidInput(f"{field!r} may only be raised")
  return modified

def changes_between(
  original: dict[str, Any], modified: dict[str, Any] | None
) -> dict[str, Any] | None:
  if modified is None:
    return None
  return {
    k: {"from": original.get(k), "to": v}
    for k, v in modified.items()
    if original.get(k) != v
  }

async def prepare_modify(
  session: AsyncSession, request_id: uuid.UUID, changes: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str | None]]:
  args, spec = (
    await session.execute(
      select(Request.args, Request.screen_spec).where(Request.id == request_id)
    )
  ).one()
  modified = apply_changes(spec, args, changes)
  try:
    screen = render_spec(spec, modified)
  except RenderError as e:
    raise InvalidInput(f"could not render the changed request: {e}") from e
  return modified, screen