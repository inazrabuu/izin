"""escalation chain

Revision ID: 1c01c7f5e67c
Revises: 48c7ad27ea67
Create Date: 2026-10-22 16:59:40.346689

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INTERVAL, JSONB

# revision identifiers, used by Alembic.
revision: str = '1c01c7f5e67c'
down_revision: str | Sequence[str] | None = '48c7ad27ea67'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.execute("UPDATE actions SET on_timeout = 'deny' WHERE on_timeout = 'escalate'")
  op.execute("ALTER TABLE actions DROP CONSTRAINT ck_actions_on_timeout_valid")
  op.execute("""
    ALTER TABLE actions ADD CONSTRAINT ck_actions_on_timeout_valid
    CHECK (on_timeout IN ('approve', 'deny'))
  """)
  op.add_column("actions", sa.Column("steps", JSONB, nullable=True))
  op.add_column("routes", sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True))
  op.add_column("requests", sa.Column("plan", JSONB, nullable=True))

  op.execute("ALTER TABLE routes DISABLE TRIGGER routes_append_only")
  op.execute("ALTER TABLE requests DISABLE TRIGGER requests_guard")
  op.execute("""
    UPDATE routes r
    SET due_at = coalesce(q.expires_at, q.created_at + a.default_timeout)
    FROM requests q JOIN actions a ON a.id = q.action_id
    WHERE q.id = r.request_id
  """)
  op.execute("""
    UPDATE requests q SET plan = jsonb_build_object(
        'on_timeout', a.on_timeout,
        'steps', jsonb_build_array(jsonb_build_object(
            'role', r.role,
            'principal_ids', to_jsonb(r.principal_ids),
            'after', extract(epoch FROM r.due_at - q.created_at)::int
        ))
    )
    FROM actions a, routes r
    WHERE a.id = q.action_id AND r.request_id = q.id AND r.step_index = 0
  """)
  op.execute("ALTER TABLE requests ENABLE TRIGGER requests_guard")
  op.execute("ALTER TABLE routes ENABLE TRIGGER routes_append_only")

  op.alter_column("routes", "due_at", nullable=False)
  op.alter_column("requests", "plan", nullable=False)
  op.drop_column("routes", "escalate_after")
  op.create_index("ix_routes_due_at", "routes", ["due_at"])


def downgrade() -> None:
  op.drop_index("ix_routes_due_at", table_name="routes")
  op.add_column("routes", sa.Column("escalate_after", INTERVAL, nullable=True))
  op.drop_column("requests", "plan")
  op.drop_column("routes", "due_at")
  op.drop_column("actions", "steps")
  op.execute("ALTER TABLE actions DROP CONSTRAINT ck_actions_on_timeout_valid")
  op.execute("""
    ALTER TABLE actions ADD CONSTRAINT ck_actions_on_timeout_valid
    CHECK (on_timeout IN ('approve', 'deny', 'escalate'))
  """)
