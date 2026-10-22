"""timeouts: system principal and auto-approve guard

Revision ID: 48c7ad27ea67
Revises: 83fb667c7cf2
Create Date: 2026-10-21 14:46:37.625290

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48c7ad27ea67'
down_revision: Union[str, Sequence[str], None] = '83fb667c7cf2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIMEOUT_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
  op.execute("""
    ALTER TABLE actions ADD CONSTRAINT ck_actions_no_auto_approve_risky 
    CHECK (NOT(on_timeout='approve' AND risk_level IN ('high', 'critical')))
  """)
  op.execute(f"""
    INSERT INTO principals (id, name, available) 
    VALUES ('{TIMEOUT_PRINCIPAL_ID}', 'izin (timeout)', false) 
    ON CONFLICT (id) DO NOTHING
  """)
  op.execute("""
    ALTER TABLE principals ADD CONSTRAINT ck_principals_system_inert 
    CHECK (
      id::text NOT LIKE '00000000-0000-0000-0000-%' 
      OR (token_hash IS NULL AND cardinality(roles) = 0)
    )
  """)

def downgrade() -> None:
  op.execute("ALTER TABLE principals DROP CONSTRAINT ck_principals_system_inert")
  op.execute(f"""
    DELETE FROM principals p WHERE p.id = '{TIMEOUT_PRINCIPAL_ID}' 
    AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.principal_id = p.id)
  """)
  op.execute("ALTER TABLE actions DROP CONSTRAINT ck_actions_no_auto_approve_risky")
