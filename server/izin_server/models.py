import enum
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
  BigInteger,
  CheckConstraint,
  Enum as SAEnum,
  ForeignKey,
  Identity,
  Index,
  MetaData,
  String,
  Text,
  UniqueConstraint,
  text
)
from sqlalchemy.dialects.postgresql import ARRAY, INTERVAL, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
  "ix": "ix_%(table_name)s_%(column_0_N_name)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s"
}

class Base(DeclarativeBase):
  metadata = MetaData(naming_convention=NAMING_CONVENTION)
  type_annotation_map = {
    dict[str, Any]: JSONB,
    datetime: TIMESTAMP(timezone=True),
    timedelta: INTERVAL,
    uuid.UUID: UUID(as_uuid=True)
  }

# ---------- enums ------------
class RiskLevel(enum.StrEnum):
  LOW = "low"
  MEDIUM = "medium"
  HIGH = "high"
  CRITICAL = "critical"

class OnTimeout(enum.StrEnum):
  APPROVE = "approve"
  DENY = "deny"
  ESCALATE = "escalate"

class RequestStatus(enum.StrEnum):
  PENDING = "PENDING"
  APPROVED = "APPROVED"
  DENIED = "DENIED"
  ESCALATED = "ESCALATED"
  EXPIRED = "EXPIRED"
  CONSUMED = "CONSUMED"

class Verdict(enum.StrEnum):
  APPROVE = "approve"
  DENY = "deny"
  MODIFY = "modify"

class OutboxStatus(enum.StrEnum):
  PENDING = "pending"
  SENT = "sent"
  FAILED = "failed"

# -------------- helpers ---------------
def str_enum(e: type[enum.StrEnum]) -> SAEnum:
  return SAEnum(
    e,
    name=e.__name__.lower(),
    native_enum=False,
    create_constraint=False,
    length=16,
    values_callable=lambda members: [m.value for m in members]
  )

def enum_check(column: str, e: type[enum.StrEnum]) -> CheckConstraint:
  allowed = ", ".join(f"'{m.value}'" for m in e)
  return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")

def uuid_pk():
  return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))

def created_at():
  return mapped_column(server_default=text("now()"))

# ----------------- config tables ------------------
class Action(Base):
  __tablename__ = "actions"
  __table_args__ = (
    enum_check("risk_level", RiskLevel),
    enum_check("on_timeout", OnTimeout)
  )
  
  id: Mapped[uuid.UUID] = uuid_pk()
  name: Mapped[str] = mapped_column(String(100), unique=True)
  display_name: Mapped[str] = mapped_column(String(200))
  description: Mapped[str | None] = mapped_column(Text)
  risk_level: Mapped[RiskLevel] = mapped_column(str_enum(RiskLevel))
  args_schema: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
  render_template: Mapped[str] = mapped_column(Text)
  blast_radius: Mapped[str | None] = mapped_column(Text)
  if_denied: Mapped[str | None] = mapped_column(Text)
  default_timeout: Mapped[timedelta] = mapped_column(server_default=text("interval '4 hours'"))
  on_timeout: Mapped[OnTimeout] = mapped_column(str_enum(OnTimeout), server_default="deny")
  created_at: Mapped[datetime] = created_at()

class Principal(Base):
  __tablename__ = "principals"

  id: Mapped[uuid.UUID] = uuid_pk()
  name: Mapped[str] = mapped_column(String(200))
  email: Mapped[str | None] = mapped_column(String(320), unique=True)
  phone: Mapped[str | None] = mapped_column(String(32))
  roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
  available: Mapped[bool] = mapped_column(server_default=text("true"))
  created_at: Mapped[datetime] = created_at()

# -------------------- the request path ------------------------
class Request(Base):
  __tablename__ = "requests"
  __table_args__ = (
    Index("ix_requests_status_expires_at", "status", "expires_at"),
    enum_check("status", RequestStatus)
  )

  id: Mapped[uuid.UUID] = uuid_pk()
  action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actions.id"))
  idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
  agent_run_id: Mapped[str] = mapped_column(String(200))
  agent_name: Mapped[str | None] = mapped_column(String(200))
  args: Mapped[dict[str, Any]]
  rationale: Mapped[str | None] = mapped_column(Text)
  context: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
  rendered: Mapped[str] = mapped_column(Text)
  status: Mapped[RequestStatus] = mapped_column(
    str_enum(RequestStatus), server_default=RequestStatus.PENDING.value
  )
  expires_at: Mapped[datetime | None]
  created_at: Mapped[datetime] = created_at()

class Route(Base):
  __tablename__ = "routes"
  __table_args__ = (
    UniqueConstraint("request_id", "step_index"),
    CheckConstraint("cardinality(principal_ids) > 0 OR role IS NOT NULL", name="has_target"),
  )

  id: Mapped[uuid.UUID] = uuid_pk()
  request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id"))
  step_index: Mapped[int] = mapped_column(server_default=text("0"))
  principal_ids: Mapped[list[uuid.UUID]] = mapped_column(
    ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
  )
  role: Mapped[str | None] = mapped_column(String(100))
  escalate_after: Mapped[timedelta | None]
  created_at: Mapped[datetime] = created_at()

class Decision(Base):
  __tablename__ = "decisions"
  __table_args__ = (
    enum_check("verdict", Verdict),
    CheckConstraint("(verdict = 'modify') = (modified_args IS NOT NULL)", name="notify_has_args"),
    CheckConstraint(
      "verdict <> 'deny' OR length(trim(coalesce(comment, ''))) > 0",
      name="deny_has_reason"
    ),
  )

  id: Mapped[uuid.UUID] = uuid_pk()
  request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id"), unique=True)
  principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("principals.id"))
  verdict: Mapped[Verdict] = mapped_column(str_enum(Verdict))
  modified_args: Mapped[dict[str, Any] | None]
  comment: Mapped[str | None] = mapped_column(Text)
  decided_at: Mapped[datetime] = created_at()
  rendered_snapshot: Mapped[str] = mapped_column(Text)

class RequestEvent(Base):
  __tablename__ = "request_events"
  __table_args__ = (
    Index("ix_request_events_request_id_at", "request_id", "at"),
  )

  id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
  request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id"))
  type: Mapped[str] = mapped_column(String(32))
  payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
  at: Mapped[datetime] = created_at()

class Outbox(Base):
  __tablename__ = "outbox"
  __table_args__ = (
    Index("ix_outbox_date", "next_attempt_at", postgresql_where=text("status = 'pending'")),
    enum_check("status", OutboxStatus)
  )

  id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
  request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id"))
  channel: Mapped[str] = mapped_column(String(32))
  payload: Mapped[dict[str, Any]]
  status: Mapped[OutboxStatus] = mapped_column(
    str_enum(OutboxStatus), server_default=OutboxStatus.PENDING.value
  )
  attempts: Mapped[int] = mapped_column(server_default=text("0"))
  next_attempt_at: Mapped[datetime] = created_at()
  last_error: Mapped[str | None] = mapped_column(Text)
  created_at: Mapped[datetime] = created_at()