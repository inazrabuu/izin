"""modify verdict

Revision ID: 65241dadd11d
Revises: 413dbb3b1a37
Create Date: 2026-10-26 20:11:45.738355

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = '65241dadd11d'
down_revision: str | Sequence[str] | None = '413dbb3b1a37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column(
    "actions",
    sa.Column("editable", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
  )
  op.add_column("requests", sa.Column("screen_spec", JSONB, nullable=True))
  op.add_column("decisions", sa.Column("modified_screen", JSONB, nullable=True))
  
  # Best-effort backfill: pre-0.2 requests never froze their templates,
  # so today's action definition is the closest thing we have.
  op.execute("ALTER TABLE requests DISABLE TRIGGER requests_guard")
  op.execute("""
  UPDATE requests q SET screen_spec = jsonb_build_object(
      'headline', a.render_template,
      'blast_radius', a.blast_radius,
      'if_denied', a.if_denied,
      'args_schema', a.args_schema,
      'editable', '[]'::jsonb
  )
  FROM actions a WHERE a.id = q.action_id
  """)
  op.execute("ALTER TABLE requests ENABLE TRIGGER requests_guard")
  op.alter_column("requests", "screen_spec", nullable=False)
  
  op.execute("""
    ALTER TABLE decisions ADD CONSTRAINT ck_decisions_modify_has_screen
    CHECK ((verdict = 'modify') = (modified_screen IS NOT NULL)) NOT VALID
  """)
  op.execute("""
    ALTER TABLE decisions ADD CONSTRAINT ck_decisions_modify_has_reason
    CHECK (verdict <> 'modify' OR (comment IS NOT NULL AND btrim(comment) <> ''))
  """)


def downgrade() -> None:
  op.execute("ALTER TABLE decisions DROP CONSTRAINT ck_decisions_modify_has_reason")
  op.execute("ALTER TABLE decisions DROP CONSTRAINT ck_decisions_modify_has_screen")
  op.drop_column("decisions", "modified_screen")
  op.drop_column("requests", "screen_spec")
  op.drop_column("actions", "editable")
