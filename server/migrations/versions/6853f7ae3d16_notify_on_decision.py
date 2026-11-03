"""notify on decision

Revision ID: 6853f7ae3d16
Revises: 459b1dfb53ab
Create Date: 2026-11-03 20:40:14.570738

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6853f7ae3d16'
down_revision: str | Sequence[str] | None = '459b1dfb53ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.execute("""
    CREATE OR REPLACE FUNCTION izin_notify_decided() RETURNS trigger AS $$
    BEGIN
        PERFORM pg_notify('izin_decided', NEW.request_id::text);
        RETURN NULL;
    END
    $$ LANGUAGE plpgsql
  """)
  op.execute("""
    CREATE TRIGGER decisions_notify
    AFTER INSERT ON decisions
    FOR EACH ROW EXECUTE FUNCTION izin_notify_decided()
  """)


def downgrade() -> None:
  op.execute("DROP TRIGGER decisions_notify ON decisions")
  op.execute("DROP FUNCTION izin_notify_decided()")