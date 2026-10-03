from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.errors import InvalidInput
from izin_server.models import Action
from izin_server.rendering import RenderError, template_variables
from izin_server.schemas import ActionIn

def validate_action_definition(body: ActionIn) -> None:
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

async def upsert_action(session: AsyncSession, name: str, body: ActionIn) -> Action:
  validate_action_definition()

  values = body.model_dump()
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