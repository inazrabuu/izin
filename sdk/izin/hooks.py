from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from izin.client import Approvals
from izin.errors import ApprovalTimeout, IzinError

ToolInput = dict[str, Any]

def _overlay(tool_input: ToolInput, args: dict[str, Any]) -> ToolInput:
  return {**tool_input, **args}

@dataclass(frozen=True)
class ToolRule:
  action: str
  to_args: Callable[[ToolInput], dict[str, Any]] = dict
  from_args: Callable[[ToolInput, dict[str, Any]], ToolInput] = _overlay
  rationale: Callable[[ToolInput], str | None] = lambda _: None

@dataclass(frozen=True)
class ToolVerdict:
  allow: bool
  reason: str
  updated_input: ToolInput | None = None
  pending_request_id: str | None = None

async def judge(
  approvals: Approvals, rule: ToolRule, tool_input: ToolInput, *, max_wait: float | None
) -> ToolVerdict:
  try:
    decision = await approvals.request(
      rule.action, rule.to_args(tool_input),
      rationale=rule.rationale(tool_input), max_wait=max_wait
    )
  except ApprovalTimeout as e:
    return ToolVerdict(
      False,
      f"Waiting for human approval (izin request {e.request_id}). "
      "Do not retry until you are told the decision is in.",
      pending_request_id=e.request_id
    )
  except IzinError as e:
    return ToolVerdict(False, f"Approval check failed ({e}); the action was not taken.")

  if not decision.approved:
    return ToolVerdict(False, f"Denied by {decision.decided_by}: {decision.comment}")
  updated = rule.from_args(tool_input, decision.args) if decision.modified else None

  return ToolVerdict(True, f"Approved by {decision.decided_by}", updated)