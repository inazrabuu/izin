import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from izin_server.models import (
  OnTimeout,
  OutboxStatus,
  RequestStatus,
  RiskLevel,
  Verdict,
)


class StepIn(BaseModel):
  role: str | None = Field(default=None, max_length=100)
  principal_ids: list[uuid.UUID] = Field(default_factory=list)
  after: timedelta

  def to_json(self) -> dict[str, Any]:
    return {
      "role": self.role,
      "principal_ids": [str(p) for p in self.principal_ids],
      "after": int(self.after.total_seconds())
    }

class RequestIn(BaseModel):
  action: str
  idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
  agent_run_id: str = Field(min_length=1, max_length=200)
  agent_name: str | None = Field(default=None, max_length=200)
  args: dict[str, Any]
  rationale: str | None = Field(default=None, max_length=1000)
  context: dict[str, Any] = Field(default_factory=dict)

class RequestOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: uuid.UUID
  action_id: uuid.UUID
  idempotency_key: str
  agent_run_id: str
  agent_name: str | None
  status: RequestStatus
  args: dict[str, Any]
  rationale: str | None
  context: dict[str, Any]
  rendered: dict[str, str | None]
  expires_at: datetime | None
  created_at: datetime

class PrincipalIn(BaseModel):
  name: str = Field(min_length=1, max_length=200)
  email: str | None = Field(default=None, max_length=320)
  phone: str | None = Field(default=None, max_length=32)
  roles: list[str] = Field(default_factory=list)

class PrincipalOut(PrincipalIn):
  model_config = ConfigDict(from_attributes=True)

  id: uuid.UUID
  available: bool
  created_at: datetime

class PrincipalCreated(PrincipalOut):
  token: str

class MeOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: uuid.UUID
  name: str
  roles: list[str]
  available: bool
  
class InboxDecisionIn(BaseModel):
  verdict: Verdict
  comment: str | None = Field(default=None, max_length=2000)
  changes: dict[str, Any] | None = None

  @model_validator(mode="after")
  def check(self):
    if self.verdict in (Verdict.DENY, Verdict.MODIFY) and not (self.comment or "").strip():
      raise ValueError(f"a {self.verdict.value} needs a reason")
    if (self.verdict == Verdict.MODIFY) != bool(self.changes):
      raise ValueError("changes go with the modify verdict, and only with it")
    return self

class InboxItem(BaseModel):
  id: uuid.UUID
  action_display_name: str
  risk_level: RiskLevel
  headline: str
  agent_name: str | None
  created_at: datetime
  expires_at: datetime | None
  due_at: datetime

class RenderedScreen(BaseModel):
  headline: str
  blast_radius: str | None = None
  if_denied: str | None = None

class Availability(BaseModel):
  available: bool

class EditableField(BaseModel):
  field: str
  only: Literal["decrease", "increase"] | None = None

class ActionIn(BaseModel):
  display_name: str = Field(max_length=200)
  description: str | None = None
  risk_level: RiskLevel
  args_schema: dict[str, Any]
  render_template: str
  blast_radius: str | None = None
  if_denied: str | None = None
  default_timeout: timedelta = timedelta(hours=4)
  on_timeout: OnTimeout = OnTimeout.DENY
  steps: list[StepIn] | None = Field(default=None, max_length=5)
  editable: list[EditableField] = Field(default_factory=list, max_length=10)

class ActionOut(ActionIn):
  model_config = ConfigDict(from_attributes=True)

  id: uuid.UUID
  name: str
  created_at: datetime

class EditableOut(BaseModel):
  field: str
  type: str
  only: str | None
  value: Any

class DecisionOut(BaseModel):
  verdict: Verdict
  comment: str | None
  principal_name: str
  decided_at: datetime
  args: dict[str, Any]
  changes: dict[str, Any] | None = None
  modified_screen: dict[str, Any] | None = None

class DecisionStatusOut(BaseModel):
  request_id: uuid.UUID
  status: RequestStatus
  decision: DecisionOut | None

class InboxDetail(BaseModel):
  id: uuid.UUID
  status: RequestStatus
  action_display_name: str
  risk_level: RiskLevel
  agent_name: str | None
  rationale: str | None
  rendered: RenderedScreen
  context: dict[str, Any]
  args: dict[str, Any]
  created_at: datetime
  expires_at: datetime | None
  similar_approved_7d: int
  decision: DecisionOut | None
  editable: list[EditableOut]
  link_can_decide: bool | None = None

class OutboxOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: int
  request_id: uuid.UUID
  event: str
  channel: str
  principal_id: uuid.UUID | None
  status: OutboxStatus
  attempts: int
  give_up_after: int
  last_error: str | None
  next_attempt_at: datetime
  created_at: datetime