"""audit exports

Revision ID: 459b1dfb53ab
Revises: 2108af10525f
Create Date: 2026-11-01 14:00:13.397859

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '459b1dfb53ab'
down_revision: str | Sequence[str] | None = '2108af10525f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_index("ix_requests_created_at", "requests", ["created_at"])
  op.create_table(
  "audit_exports",
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("format", sa.String(8), nullable=False),
    sa.Column("filters", JSONB, nullable=False),
    sa.Column("row_count", sa.Integer, nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
  )
  op.execute("""
    CREATE TRIGGER audit_exports_append_only
    BEFORE UPDATE OR DELETE ON audit_exports
    FOR EACH ROW EXECUTE FUNCTION izin_forbid_mutation()
  """)
  op.execute("""
    CREATE TRIGGER audit_exports_no_truncate
    BEFORE TRUNCATE ON audit_exports
    FOR EACH STATEMENT EXECUTE FUNCTION izin_forbid_mutation()
  """)


def downgrade() -> None:
  op.drop_table("audit_exports")  # its triggers go with it
  op.drop_index("ix_requests_created_at", table_name="requests")
