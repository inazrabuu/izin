import re
from typing import Any

import httpx

from izin.client import Approvals
from izin.hooks import ToolRule, judge


class ClaudeAgentApprovals:
  def __init__(
    self, *, url: str, token: str, rules: dict[str, ToolRule],
    agent_name: str | None = None, max_wait: float = 30.0, http: httpx.AsyncClient | None = None,
  ) -> None:
    self._url, self._token, self._agent_name = url, token, agent_name
    self._rules, self._max_wait = rules, max_wait
    self._owns_http = http is None
    self._http = http or httpx.AsyncClient(base_url=url, timeout=max_wait + 45)
    self._runs: dict[str, Approvals] = {}

  def _run_for(self, session_id: str) -> Approvals:
    # One Approvals per session: its sequence counter must survive across hook calls.
    if session_id not in self._runs:
      self._runs[session_id] = Approvals(
        url=self._url, token=self._token, agent_run_id=f"claude:{session_id}",
        agent_name=self._agent_name, http=self._http,
      )
    return self._runs[session_id]

  async def pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    rule = self._rules.get(input_data["tool_name"])
    if rule is None:
      return {}  # not a protected tool
    verdict = await judge(
      self._run_for(input_data["session_id"]), rule, input_data["tool_input"],
      max_wait=self._max_wait,
    )
    output: dict[str, Any] = {
      "hookEventName": "PreToolUse",
      "permissionDecision": "allow" if verdict.allow else "deny",
      "permissionDecisionReason": verdict.reason,
    }
    if verdict.updated_input is not None:
      output["updatedInput"] = verdict.updated_input
    return {"hookSpecificOutput": output}

  def matcher(self) -> str:
    return "|".join(re.escape(name) for name in sorted(self._rules))

  def hooks(self) -> dict[str, list[Any]]:
    try:
      from claude_agent_sdk import HookMatcher
    except ImportError as e:
      raise ImportError("The Claude Agent SDK adapter needs it: pip install izin['claude-agent']") from e
    return {"PreToolUse": [HookMatcher(matcher=self.matcher(), hooks=[self.pre_tool_use])]}

  async def wait_until_decided(self, request_id: str, *, max_wait: float | None = None) -> None:
    waiter = Approvals(
      url=self._url, token=self._token, agent_run_id="claude-resumer", http=self._http
    )
    await waiter.wait(request_id, max_wait=max_wait)

  async def aclose(self) -> None:
    if self._owns_http:
      await self._http.aclose()