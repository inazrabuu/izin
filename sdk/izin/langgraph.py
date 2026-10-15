import hashlib
from collections.abc import Mapping
from typing import Any

import httpx

try:
  from langgraph.types import Command, interrupt
except ImportError as e:
  raise ImportError("The LangGraph adapter needs LangGraph: pip install izin['langgraph']") from e

from izin.client import Approvals
from izin.models import Decision

MAX_RUN_ID = 200

def run_id_from(config: Mapping[str, Any]) -> str:
  configurable = config.get("configurable") or {}
  metadata = config.get("metadata") or {}

  thread_id = configurable.get("thread_id")
  if not thread_id:
    raise ValueError(
      "izin needs a LangGraph thread_id: compile the graph with a checkpointer and "
      "invoke it with config={'configurable': {'thread_id': ...}}"
    )
  step, node = metadata.get("langgraph_step"), metadata.get("langgraph_node")
  if step is None or node is None:
    raise ValueError("call in_node(config) from inside a node, with the config LangGraph passes in")

  run_id = f"lg:{thread_id}:{configurable.get('checkpoint_ns') or ''}:{step}:{node}"
  if len(run_id) > MAX_RUN_ID:
    run_id = f"lg:{thread_id[:40]}:{hashlib.sha256(run_id.encode()).hexdigest()[:40]}"
  return run_id

class NodeApprovals:
  def __init__(self, approvals: Approvals):
    self._approvals = approvals

  async def require(
    self,
    action: str,
    args: dict[str, Any],
    *,
    rationale: str | None = None,
    context: dict[str, Any] | None = None
  ) -> Decision:
    while True:
      outcome = await self._approvals.check(action, args, rationale=rationale, context=context)
      if isinstance(outcome, Decision):
        return outcome

      interrupt(
        {"izin_request_id": outcome.request_id, "action": action, "headline": outcome.headline}
      )

class LangGraphApprovals:
  def __init__(
      self,
      *,
      url: str,
      token: str,
      agent_name: str | None = None,
      http: httpx.AsyncClient | None = None
  ):
    self._url, self._token, self._agent_name = url, token, agent_name
    self._owns_http = http is None
    self._http = http or httpx.AsyncClient(base_url=url, timeout=45)

  def in_node(self, config: Mapping[str, Any]) -> NodeApprovals:
    return NodeApprovals(
      Approvals(
        url=self._url,
        token=self._token,
        agent_run_id=run_id_from(config),
        agent_name=self._agent_name,
        http=self._http
      )
    )

  async def wait_until_decided(self, request_id: str, *, max_wait: float | None = None) -> None:
    waiter = Approvals(
      url=self._url, token=self._token, agent_run_id="langgraph-resumer", http=self._http
    )
    await waiter.wait(request_id, max_wait=max_wait)

  async def resume_when_decided(self, graph, config, *, max_wait: float | None = None):
    for request_id in await pending_request_ids(graph, config):
      await self.wait_until_decided(request_id, max_wait=max_wait)
    return await graph.ainvoke(Command(resume=True), config)

  async def aclose(self) -> None:
    if self._owns_http:
      await self._http.aclose()

async def pending_request_ids(graph, config) -> list[str]:
  snapshot = await graph.aget_state(config)
  return [
    i.value["izin_request_id"]
    for task in snapshot.tasks
    for i in task.interrupts
    if isinstance(i.value, Mapping) and "izin_request_id" in i.value
  ]