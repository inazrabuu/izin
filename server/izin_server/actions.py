from datetime import timedelta

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.errors import InvalidInput
from izin_server.models import Action, OnTimeout, RiskLevel
from izin_server.rendering import RenderError, template_variables
from izin_server.schemas import ActionIn
from izin_server.system import SYSTEM_PRINCIPAL_IDS

NO_AUTO_APPROVE = (RiskLevel.HIGH, RiskLevel.CRITICAL)
EDITABLE_TYPES = {"number", "integer", "string"}

def validate_action_definition(body: ActionIn) -> None:
  if body.on_timeout == OnTimeout.APPROVE and body.risk_level in NO_AUTO_APPROVE:
    raise InvalidInput(
      f"on_timeout 'approve' is not allowed for {body.risk_level.value}-risk actions, use 'deny' or 'escalate'."
    )
  
  schema = body.args_schema
  try:
    Draft202012Validator.check_schema(schema)
  except SchemaError as e:
    raise InvalidInput(f"args_schema is not a valid JSON Schema: {e.message}") from e

  if schema.get("type") != "object":
    raise InvalidInput('args_schema must have "type": "object"')

  required = set(schema.get("required", []))
  templates = {
    "render_template": body.render_template,
    "blast_radius": body.blast_radius,
    "if_denied": body.if_denied
  }

  for field, source in templates.items():
    if not source:
      continue

    try:
      used = template_variables(source)
    except RenderError as e:
      raise InvalidInput(f"{field}: {e}") from e

    missing = used - required
    if missing:
      raise InvalidInput(f"{field} uses {sorted(missing)}, which args_schema does not lists as required")

  if body.steps is not None:
    if "default_timeout" in body.model_fields_set:
      raise InvalidInput("set either default_timeout or steps, not both")
    if not body.steps:
      raise InvalidInput("steps must not be empty")
    for i, step in enumerate(body.steps):
      if not step.role and not step.principal_ids:
        raise InvalidInput(f"steps[{i}] needs a role or principal_ids")
      if step.after < timedelta(0):
        raise InvalidInput(f"steps[{i}].after must not be negative")
      if SYSTEM_PRINCIPAL_IDS & set(step.principal_ids):
        raise InvalidInput(f"steps[{i}] routes to a system principal")
  
  props = body.args_schema.get("properties", {})
  required = set(body.args_schema.get("required", []))
  seen: set[str] = set()
  for e in body.editable:
    if e.field in seen:
      raise InvalidInput(f"editable list {e.field!r} twice")
    seen.add(e.field)
    kind = props.get(e.field, {}).get("type")
    if e.field not in required or kind not in EDITABLE_TYPES:
      raise InvalidInput(
        f"editable field {e.field!r} must be a required number, integer or string in args_schema"
      )
    if e.only and kind not in ("number", "integer"):
      raise InvalidInput(f"editable field {e.field!r}: 'only' needs a number or integer")

async def upsert_action(session: AsyncSession, name: str, body: ActionIn) -> Action:
  validate_action_definition(body)

  values = body.model_dump(exclude={"steps"})
  values["steps"] = None if body.steps is None else [s.to_json() for s in body.steps]
  stmt = (
    pg_insert(Action)
    .values(name=name, **values)
    .on_conflict_do_update(index_elements=[Action.name], set_=values)
    .returning(Action)
    .execution_options(populate_existing=True)
  )
  action = (await session.execute(stmt)).scalar_one()
  await session.commit()
  return action