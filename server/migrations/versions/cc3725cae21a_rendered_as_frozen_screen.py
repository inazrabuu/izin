"""rendered as frozen screen

Revision ID: cc3725cae21a
Revises: 831fe903b26a
Create Date: 2026-09-30 20:53:50.665065

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'cc3725cae21a'
down_revision: Union[str, Sequence[str], None] = '831fe903b26a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "requests", "rendered",
        type_=postgresql.JSONB(),
        postgresql_using="jsonb_build_object('headline', rendered)",
    )
    op.alter_column(
        "decisions", "rendered_snapshot",
        type_=postgresql.JSONB(),
        postgresql_using="jsonb_build_object('headline', rendered_snapshot)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "requests", "rendered",
        type_=sa.Text(),
        postgresql_using="rendered->>'headline'",
    )
    op.alter_column(
        "decisions", "rendered_snapshot",
        type_=sa.Text(),
        postgresql_using="rendered_snapshot->>'headline'",
    )
