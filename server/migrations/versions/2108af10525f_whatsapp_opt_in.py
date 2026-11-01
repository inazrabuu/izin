"""whatsapp opt-in

Revision ID: 2108af10525f
Revises: 3ceef6a330a4
Create Date: 2026-11-01 09:55:08.129426

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2108af10525f'
down_revision: str | Sequence[str] | None = '3ceef6a330a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column(
    "principals",
    sa.Column("whatsapp_opted_in_at", sa.TIMESTAMP(timezone=True), nullable=True),
  )

def downgrade() -> None:
  op.drop_column("principals", "whatsapp_opted_in_at")