"""request status transitions

Revision ID: 831fe903b26a
Revises: ec454021f6b8
Create Date: 2026-09-27 16:45:27.402755

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '831fe903b26a'
down_revision: Union[str, Sequence[str], None] = 'ec454021f6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GUARD_WITH_TRANSITIONS = """
CREATE OR REPLACE FUNCTION izin_requests_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    allowed text[];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'izin: requests are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'izin: only requests.status may change'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        allowed := CASE OLD.status
            WHEN 'PENDING'   THEN ARRAY['APPROVED', 'DENIED', 'ESCALATED', 'EXPIRED']
            WHEN 'ESCALATED' THEN ARRAY['PENDING']
            WHEN 'EXPIRED'   THEN ARRAY['APPROVED', 'DENIED']   -- on_timeout (§7)
            WHEN 'APPROVED'  THEN ARRAY['CONSUMED']
            WHEN 'DENIED'    THEN ARRAY['CONSUMED']
            ELSE ARRAY[]::text[]                                -- CONSUMED is final
        END;
        IF NOT (NEW.status = ANY (allowed)) THEN
            RAISE EXCEPTION 'izin: illegal status transition % -> %', OLD.status, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END $$;
"""

GUARD_ORIGINAL = """
CREATE OR REPLACE FUNCTION izin_requests_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'izin: requests are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'izin: only requests.status may change'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
"""

def upgrade() -> None:
    """Upgrade schema."""
    op.execute(GUARD_WITH_TRANSITIONS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(GUARD_ORIGINAL)
