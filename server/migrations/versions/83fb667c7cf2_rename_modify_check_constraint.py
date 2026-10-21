"""rename modify check constraint

Revision ID: 83fb667c7cf2
Revises: 661f4d8ea9ff
Create Date: 2026-10-20 20:17:49.581319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83fb667c7cf2'
down_revision: Union[str, Sequence[str], None] = '661f4d8ea9ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.execute(
    "ALTER TABLE decisions RENAME CONSTRAINT "
    "ck_decisions_notify_has_args TO ck_decisions_modify_has_args"
  )


def downgrade() -> None:
  op.execute(
    "ALTER TABLE decisions RENAME CONSTRAINT "
    "ck_decisions_modify_has_args TO ck_decisions_notify_has_args"
  )
