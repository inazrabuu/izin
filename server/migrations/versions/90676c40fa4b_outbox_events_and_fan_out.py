"""outbox events and fan-out

Revision ID: 90676c40fa4b
Revises: 65241dadd11d
Create Date: 2026-10-29 14:50:17.677449

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '90676c40fa4b'
down_revision: str | Sequence[str] | None = '65241dadd11d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("outbox", sa.Column("event", sa.String(64), nullable=True))
  op.add_column("outbox", sa.Column(
    "principal_id", UUID(as_uuid=True), sa.ForeignKey("principals.id"), nullable=True
  ))
  op.add_column("outbox", sa.Column("dedupe_key", sa.Text, nullable=True))
  op.add_column("outbox", sa.Column(
    "give_up_after", sa.Integer, nullable=False, server_default=sa.text("8")
  ))
  
  # outbox is mutable delivery state, not history, so a plain UPDATE is fine here
  op.execute("""
    UPDATE outbox
    SET event = coalesce(payload->>'event', 'request.created'),
        dedupe_key = 'legacy:' || id
  """)
  op.alter_column("outbox", "event", nullable=False)
  op.alter_column("outbox", "dedupe_key", nullable=False)
  op.create_unique_constraint("uq_outbox_dedupe_key", "outbox", ["dedupe_key"])



def downgrade() -> None:
  op.drop_constraint("uq_outbox_dedupe_key", "outbox", type_="unique")
  op.drop_column("outbox", "give_up_after")
  op.drop_column("outbox", "dedupe_key")
  op.drop_column("outbox", "principal_id")
  op.drop_column("outbox", "event")
