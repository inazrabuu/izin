"""routing system principal

Revision ID: 413dbb3b1a37
Revises: 1c01c7f5e67c
Create Date: 2026-10-23 20:38:44.430120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '413dbb3b1a37'
down_revision: Union[str, Sequence[str], None] = '1c01c7f5e67c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROUTING_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000002"

def upgrade() -> None:
  op.execute(f"""
    ISNERT INTO principals (id, name, available) 
    VALUES ('{ROUTING_PRINCIPAL_ID}', 'izin (routing)', false) 
    ON CONFLICT (id) DO NOTHING
  """)


def downgrade() -> None:
  op.execute(f"""
    DELETE FROM principals p 
    WHERE p.id = '{ROUTING_PRINCIPAL_ID}' 
    AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.principal_id = p.id)
  """)
