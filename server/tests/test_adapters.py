import uuid
from types import SimpleNamespace

import pytest

from helpers import ARGS, as_principal, post_decision
from izin import ToolRule, ToolVerdict
from izin import crewai as crewai_adapter
from izin.claude_agent import ClaudeAgentApprovals
from izin.crewai import CrewAIApprovals
from test_escalation import put_action, unique


def claude(sdk_http, action: str, **kw) -> ClaudeAgentApprovals:
  return ClaudeAgentApprovals(
    url="http://test", token="test-token", http=sdk_http,
    rules={"refund_order": ToolRule(action=action, **kw)}, max_wait=0,
  )

def call(session: str = "s1", tool: str = "refund_order", tool_input: dict | None = None) -> dict:
  return {"tool_name": tool, "session_id": session, "tool_input": tool_input or dict(ARGS)}

def decision_of(output: dict) -> tuple[str, str]:
  hook = output["hookSpecificOutput"]
  return hook["permissionDecision"], hook["permissionDecisionReason"]

async def protected_action(client, **overrides) -> str:
  action = unique("tool")
  await put_action(client, action, **overrides)
  return action

async def pending_id(reason: str) -> str:
  return reason.split("izin request ")[1].split(")")[0]

# ---------- Claude Agent SDK ----------

async def test_unprotected_tool_passes_through(sdk_http):
  adapter = claude(sdk_http, "unused")
  assert await adapter.pre_tool_use(call(tool="Read"), None, None) == {}

async def test_pending_approval_denies_with_the_request_id(client, sdk_http, approver):
  adapter = claude(sdk_http, await protected_action(client))
  verdict, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  assert verdict == "deny"
  assert "Waiting for human approval" in reason

async def test_retry_after_approval_is_allowed(client, sdk_http, approver):
  adapter = claude(sdk_http, await protected_action(client))
  _, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  await post_decision(client, {"id": await pending_id(reason)}, approver)
  verdict, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  assert (verdict, reason) == ("allow", "Approved by Ana")

async def test_denial_reason_reaches_the_model(client, sdk_http, approver):
  adapter = claude(sdk_http, await protected_action(client))
  _, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  await post_decision(
    client, {"id": await pending_id(reason)}, approver, verdict="deny", comment="Already refunded."
  )
  verdict, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  assert verdict == "deny" and "Already refunded." in reason

async def test_modified_args_become_updated_input(client, sdk_http, approver):
  action = await protected_action(client, editable=[{"field": "amount_idr", "only": "decrease"}])
  adapter = claude(sdk_http, action)
  _, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  r = await client.post(
    f"/v1/inbox/requests/{await pending_id(reason)}/decision",
    json={"verdict": "modify", "comment": "Partial.", "changes": {"amount_idr": 4_000_000}},
    headers=as_principal(approver),
  )
  assert r.status_code == 200, r.text
  out = (await adapter.pre_tool_use(call(), None, None))["hookSpecificOutput"]
  assert out["permissionDecision"] == "allow"
  assert out["updatedInput"] == {**ARGS, "amount_idr": 4_000_000}

async def test_izin_errors_fail_closed(client, sdk_http):
  action = await protected_action(client)
  adapter = claude(sdk_http, action, to_args=lambda t: {**t, "nonce": uuid.uuid4()})
  verdict, reason = decision_of(await adapter.pre_tool_use(call(), None, None))
  assert verdict == "deny" and "Approval check failed" in reason

async def test_each_session_is_its_own_run(client, sdk_http, approver):
  adapter = claude(sdk_http, await protected_action(client))
  _, first = decision_of(await adapter.pre_tool_use(call(session="a"), None, None))
  _, second = decision_of(await adapter.pre_tool_use(call(session="b"), None, None))
  assert await pending_id(first) != await pending_id(second)

def test_matcher_lists_only_protected_tools():
  adapter = ClaudeAgentApprovals(
    url="http://test", token="t",
    rules={"mcp__shop__refund": ToolRule("refund_order"), "Bash": ToolRule("run_shell")},
  )
  assert adapter.matcher() == "Bash|mcp__shop__refund"

# ---------- CrewAI ----------

@pytest.fixture
def crew(monkeypatch):
  verdicts: list[ToolVerdict] = []

  async def fake_judge(approvals, rule, tool_input, *, max_wait):
    return verdicts.pop(0)

  monkeypatch.setattr(crewai_adapter, "judge", fake_judge)
  adapter = CrewAIApprovals(
    url="http://test", token="t", agent_run_id="crew-1",
    rules={"refund_order": ToolRule("refund_order")},
  )
  yield adapter, verdicts
  adapter.close()

def test_crewai_unprotected_tool_passes_through(crew):
  adapter, _ = crew
  assert adapter.before_tool_call(SimpleNamespace(tool_name="search", tool_input={})) is None

def test_crewai_denial_blocks_the_tool(crew):
  adapter, verdicts = crew
  verdicts.append(ToolVerdict(False, "Denied by Ana: no."))
  context = SimpleNamespace(tool_name="refund_order", tool_input=dict(ARGS))
  assert adapter.before_tool_call(context) is False

def test_crewai_modification_rewrites_input_in_place(crew):
  adapter, verdicts = crew
  verdicts.append(ToolVerdict(True, "Approved by Ana", {**ARGS, "amount_idr": 4_000_000}))
  tool_input = dict(ARGS)
  context = SimpleNamespace(tool_name="refund_order", tool_input=tool_input)
  assert adapter.before_tool_call(context) is None
  assert tool_input["amount_idr"] == 4_000_000  # the SAME dict CrewAI holds