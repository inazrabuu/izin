import asyncio
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from izin.client import Approvals
from izin.hooks import ToolRule, judge

log = logging.getLogger("izin.crewai")
T = TypeVar("T")

class CrewAIApprovals:
  """CrewAI's tool hooks are synchronous; izin is async. A private event loop on its own
  thread bridges the two, so one Approvals (and its connection pool) lives on one loop."""

  def __init__(
    self, *, url: str, token: str, agent_run_id: str, rules: dict[str, ToolRule],
    agent_name: str | None = None, max_wait: float | None = None,
  ) -> None:
    self._rules, self._max_wait = rules, max_wait
    self._loop = asyncio.new_event_loop()
    self._thread = threading.Thread(target=self._loop.run_forever, name="izin-crewai", daemon=True)
    self._thread.start()
    self._approvals = self._run(self._make(url, token, agent_run_id, agent_name))

  @staticmethod
  async def _make(url: str, token: str, agent_run_id: str, agent_name: str | None) -> Approvals:
    return Approvals(url=url, token=token, agent_run_id=agent_run_id, agent_name=agent_name)

  def _run(self, coro: Coroutine[Any, Any, T]) -> T:
    # Never call from the adapter's own loop: it would wait on itself.
    return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

  def before_tool_call(self, context: Any) -> bool | None:
    rule = self._rules.get(context.tool_name)
    if rule is None:
      return None
    verdict = self._run(judge(self._approvals, rule, context.tool_input, max_wait=self._max_wait))
    if not verdict.allow:
      log.warning("izin blocked %s: %s", context.tool_name, verdict.reason)
      return False
    if verdict.updated_input is not None:
      context.tool_input.clear()  # CrewAI reads the same dict: change it in place
      context.tool_input.update(verdict.updated_input)
    return None

  def register(self) -> None:
    try:
      from crewai.hooks import register_before_tool_call_hook
    except ImportError as e:
      raise ImportError("The CrewAI adapter needs CrewAI: pip install izin['crewai']") from e
    register_before_tool_call_hook(self.before_tool_call)

  def close(self) -> None:
    self._run(self._approvals.aclose())
    self._loop.call_soon_threadsafe(self._loop.stop)
    self._thread.join(timeout=5)
    self._loop.close()