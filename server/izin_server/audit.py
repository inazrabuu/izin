import csv
import hashlib
import io
import json
import operator
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import Numeric, Select, case, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.db import SessionLocal
from izin_server.errors import InvalidInput, NotFound
from izin_server.models import (
  Action,
  AuditExport,
  Decision,
  Principal,
  Request,
  RequestEvent,
  Route,
  Verdict,
)
from izin_server.modify import changes_between
from izin_server.system import SYSTEM_PRINCIPAL_IDS

Format = Literal["csv", "jsonl"]
MAX_RANGE = timedelta(days=366)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
ARG_FILTER = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]{0,63})(?P<op>>=|<=|>|<|=)(?P<value>.+)$")
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "=": operator.eq}

COLUMNS = [
  "request_id", "created_at", "action", "risk_level", "agent_name", "agent_run_id", "status",
  "rationale", "headline", "requested_args", "approved_args", "changes",
  "verdict", "decided_by", "automatic", "decided_via", "comment", "decided_at",
  "consumed_at", "final_step",
]

@dataclass(frozen=True)
class ArgFilter:
  key: str
  op: str
  value: str

  @classmethod
  def parse(cls, raw: str) -> "ArgFilter":
    match = ARG_FILTER.match(raw)
    if match is None:
      raise InvalidInput(f"arg filter {raw!r} must look like amount_idr>=5000000")
    parsed = cls(match["key"], match["op"], match["value"])
    if parsed.number is None and parsed.op != "=":
      raise InvalidInput(f"arg filter {raw!r}: only = works on text values")
    return parsed

  @property
  def number(self) -> Decimal | None:
    try:
      return Decimal(self.value)
    except InvalidOperation:
      return None

  def clause(self, args):
    element = args[self.key]
    if self.number is not None:
      numeric = case((func.jsonb_typeof(element) == "number", element.astext.cast(Numeric)))
      return OPS[self.op](numeric, self.number)
    return element.astext == self.value

@dataclass(frozen=True)
class ExportFilter:
  start: datetime
  end: datetime
  actions: tuple[str, ...] = ()
  agent_name: str | None = None
  verdict: Verdict | None = None
  args: tuple[ArgFilter, ...] = ()

  @classmethod
  def build(
    cls, start: datetime, end: datetime, actions: Iterable[str] | None,
    agent_name: str | None, verdict: Verdict | None, args: Iterable[str] | None,
  ) -> "ExportFilter":
    for name, ts in (("start", start), ("end", end)):
      if ts.tzinfo is None:
        raise InvalidInput(f"{name} needs a timezone offset, e.g. 2026-04-01T00:00:00+07:00")
    if end <= start:
      raise InvalidInput("end must be after start")
    if end - start > MAX_RANGE:
      raise InvalidInput("export at most 366 days at a time")
    return cls(
      start, end, tuple(actions or ()), agent_name, verdict,
      tuple(ArgFilter.parse(a) for a in args or ()),
    )

  def describe(self) -> dict[str, Any]:
    return {
      "start": self.start.isoformat(), "end": self.end.isoformat(),
      "actions": list(self.actions), "agent_name": self.agent_name,
      "verdict": self.verdict.value if self.verdict else None,
      "args": [f"{a.key}{a.op}{a.value}" for a in self.args],
    }

def export_query(f: ExportFilter) -> Select:
  effective = func.coalesce(Decision.modified_args, Request.args, type_=JSONB)
  decided_via = (
    select(func.coalesce(RequestEvent.payload["via"].astext, RequestEvent.payload["reason"].astext))
    .where(RequestEvent.request_id == Request.id, RequestEvent.type == "decided")
    .limit(1).scalar_subquery()
  )
  consumed_at = (
    select(func.min(RequestEvent.at))
    .where(RequestEvent.request_id == Request.id, RequestEvent.type == "consumed")
    .scalar_subquery()
  )
  final_step = (
    select(func.max(Route.step_index)).where(Route.request_id == Request.id).scalar_subquery()
  )
  query = (
    select(
      Request.id, Request.created_at, Request.agent_name, Request.agent_run_id,
      Request.status, Request.rationale, Request.rendered, Request.args,
      Action.name.label("action"), Action.risk_level,
      Decision.verdict, Decision.modified_args, Decision.comment,
      Decision.decided_at, Decision.principal_id,
      Principal.name.label("decided_by"),
      decided_via.label("decided_via"), consumed_at.label("consumed_at"),
      final_step.label("final_step"),
    )
    .join(Action, Action.id == Request.action_id)
    .outerjoin(Decision, Decision.request_id == Request.id)
    .outerjoin(Principal, Principal.id == Decision.principal_id)
    .where(Request.created_at >= f.start, Request.created_at < f.end)
    .order_by(Request.created_at, Request.id)
  )
  if f.actions:
    query = query.where(Action.name.in_(f.actions))
  if f.agent_name:
    query = query.where(Request.agent_name == f.agent_name)
  if f.verdict:
    query = query.where(Decision.verdict == f.verdict)
  for arg in f.args:
    query = query.where(arg.clause(effective))
  return query

def _iso(ts: datetime | None) -> str | None:
  return ts.isoformat() if ts else None

def to_record(row) -> dict[str, Any]:
  approved = row.verdict in (Verdict.APPROVE, Verdict.MODIFY)
  return {
    "request_id": str(row.id),
    "created_at": _iso(row.created_at),
    "action": row.action,
    "risk_level": row.risk_level.value,
    "agent_name": row.agent_name,
    "agent_run_id": row.agent_run_id,
    "status": row.status.value,
    "rationale": row.rationale,
    "headline": row.rendered["headline"],
    "requested_args": row.args,
    "approved_args": (row.modified_args or row.args) if approved else None,
    "changes": changes_between(row.args, row.modified_args),
    "verdict": row.verdict.value if row.verdict else None,
    "decided_by": row.decided_by,
    "automatic": (row.principal_id in SYSTEM_PRINCIPAL_IDS) if row.principal_id else None,
    "decided_via": row.decided_via,
    "comment": row.comment,
    "decided_at": _iso(row.decided_at),
    "consumed_at": _iso(row.consumed_at),
    "final_step": row.final_step,
  }

def _cell(value: Any) -> str:
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, (dict, list)):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
  if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
    return "'" + value  # Excel must read this as text, never as a formula
  return str(value)

def _csv_line(values: Iterable[Any]) -> bytes:
  buffer = io.StringIO()
  csv.writer(buffer).writerow([_cell(v) for v in values])
  return buffer.getvalue().encode()

async def stream_export(f: ExportFilter, export_id: uuid.UUID, fmt: Format) -> AsyncIterator[bytes]:
  digest = hashlib.sha256()
  rows = 0
  async with SessionLocal() as session:  # its own session: the request's is closed by now
    if fmt == "csv":
      header = _csv_line(COLUMNS)
      digest.update(header)
      yield header
    result = await session.stream(export_query(f).execution_options(yield_per=500))
    async for row in result:
      record = to_record(row)
      if fmt == "csv":
        chunk = _csv_line(record[c] for c in COLUMNS)
      else:
        chunk = (json.dumps(record, ensure_ascii=False) + "\n").encode()
      digest.update(chunk)
      rows += 1
      yield chunk
    session.add(AuditExport(
      id=export_id, format=fmt, filters=f.describe(), row_count=rows, sha256=digest.hexdigest(),
    ))
    await session.commit()

async def get_export(session: AsyncSession, export_id: uuid.UUID) -> AuditExport:
  export = await session.get(AuditExport, export_id)
  if export is None:
    raise NotFound("export not found")
  return export