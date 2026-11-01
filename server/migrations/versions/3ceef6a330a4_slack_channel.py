"""slack channel

Revision ID: 3ceef6a330a4
Revises: 90676c40fa4b
Create Date: 2026-10-31 17:55:42.659399

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '3ceef6a330a4'
down_revision: str | Sequence[str] | None = '90676c40fa4b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column("principals", sa.Column("slack_user_id", sa.String(32), nullable=True))
  op.create_unique_constraint("uq_principals_slack_user_id", "principals", ["slack_user_id"])
  op.create_table(
    "slack_messages",
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("request_id", UUID(as_uuid=True), sa.ForeignKey("requests.id"), nullable=False),
    sa.Column("principal_id", UUID(as_uuid=True), sa.ForeignKey("principals.id"), nullable=False),
    sa.Column("step", sa.Integer, nullable=False),
    sa.Column("channel", sa.String(32), nullable=False),
    sa.Column("ts", sa.String(32), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("channel", "ts", name="uq_slack_messages_channel_ts"),
  )
  op.create_index("ix_slack_messages_request_id", "slack_messages", ["request_id"])


def downgrade() -> None:
  op.drop_index("ix_slack_messages_request_id", table_name="slack_messages")
  op.drop_table("slack_messages")
  op.drop_constraint("uq_principals_slack_user_id", "principals", type_="unique")
  op.drop_column("principals", "slack_user_id")
