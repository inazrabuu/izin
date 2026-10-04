import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from izin_server.models import OnTimeout, RequestStatus, RiskLevel, Verdict

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

class ActionOut(ActionIn):
  model_config = ConfigDict(from_attributes=True)

  id: uuid.UUID
  name: str
  created_at: datetime

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

class DecicionIn(BaseModel):
  principal_id: uuid.UUID
  verdict: Verdict
  comment: str | None = Field(default=None, max_length=2000)

  @model_validator(mode="after")
  def deny_needs_reason(self):
    if self.verdict == Verdict.DENY and not (self.comment or "").strip():
      raise ValueError("a deny needs a reason")
    return self

class DecisionOut(BaseModel):
  verdict: Verdict
  comment: str | None
  principal_name: str
  decided_at: datetime
  args: dict[str, Any]

class DecisionStatusOut(BaseModel):
  request_id: uuid.UUID
  status: RequestStatus
  decision: DecisionOut | None