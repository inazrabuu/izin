import uuid
from typing import TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import func, select

from helpers import ARGS, post_decision
from izin.langgraph import LangGraphApprovals, pending_request_ids, run_id_from
from izin_server.db import SessionLocal
from izin_server.models import Request

class State(TypedDict, total=False):
  order_id: str
  amount_idr: int
  customer: dict
  outcome: str

def build_graph(izin: LangGraphApprovals, action: str, checkpointer, runs: list):
  async def approve_and_refund(state: State, config: RunnableConfig) -> State:
    runs.append(1)
    approvals = izin.in_node(config)
    decision = await approvals.require(
      action,
      args={k: state[k] for k in ("order_id", "amount_idr", "customer")},
      rationale="Courier lost it."
    )
    if decision.approved:
      return {"outcome": f"refunded:{decision.idempotency_key}"}
    return {"outcome": f"denied:{decision.comment}"}

  builder = StateGraph(State)
  builder.add_node("approve_and_refund", approve_and_refund)
  builder.add_edge(START, "approve_and_refund")
  builder.add_edge("approve_and_refund", END)
  return builder.compile(checkpointer=checkpointer)

@pytest.fixture
def izin(sdk_http):
  return LangGraphApprovals(url="http://test", token="test-token", agent_name="lg-test", http=sdk_http)

def thread_config() -> dict:
  return {"configurable": {"thread_id": f"thread-{uuid.uuid4().hex}"}}

async def requests_in_thread(config) -> int:
  thread_id = config["configurable"]["thread_id"]
  async with SessionLocal() as s:
    return await s.scalar(
      select(func.count())
      .select_from(Request)
      .where(Request.agent_run_id.like(f"lg:{thread_id}:%"))
    )

async def test_graph_pauses_then_resumes_after_approvals(client, izin, action, approver):
  runs: list = []
  graph = build_graph(izin, action, InMemorySaver(), runs)
  config = thread_config()

  result = await graph.ainvoke(ARGS, config)
  assert "outcome" not in result
  [rid] = await pending_request_ids(graph, config)

  await post_decision(client, {"id": rid}, approver)
  result = await izin.resume_when_decided(graph, config)

  assert result["outcome"] == f"refunded:{rid}"
  assert len(runs) == 2
  assert await requests_in_thread(config) == 1

async def test_a_different_process_can_resume(client, izin, sdk_http, action, approver):
  checkpointer = InMemorySaver()
  config = thread_config()

  first = build_graph(izin, action, checkpointer, [])
  await first.ainvoke(ARGS, config)
  [rid] = await pending_request_ids(first, config)
  await post_decision(client, {"id": rid}, approver)

  fresh = LangGraphApprovals(url="http://test", token="test-token", http=sdk_http)
  second = build_graph(fresh, action, checkpointer, [])
  result = await fresh.resume_when_decided(second, config)
  assert result["outcome"] == f"refunded:{rid}"

async def test_early_resume_pauses_again_without_duplicates(client, izin, action, approver):
  graph = build_graph(izin, action, InMemorySaver(), [])
  config = thread_config()
  await graph.ainvoke(ARGS, config)
  [rid] = await pending_request_ids(graph, config)

  result = await graph.ainvoke(Command(resume=True), config)  # nobody has decided yet
  assert "outcome" not in result
  assert await pending_request_ids(graph, config) == [rid]
  assert await requests_in_thread(config) == 1

  await post_decision(client, {"id": rid}, approver)
  result = await izin.resume_when_decided(graph, config)
  assert result["outcome"] == f"refunded:{rid}"


async def test_denial_reaches_the_graph(client, izin, action, approver):
  graph = build_graph(izin, action, InMemorySaver(), [])
  config = thread_config()
  await graph.ainvoke(ARGS, config)
  [rid] = await pending_request_ids(graph, config)

  await post_decision(
      client, {"id": rid}, approver, verdict="deny", comment="Tracking shows delivered."
  )
  result = await izin.resume_when_decided(graph, config)
  assert result["outcome"] == "denied:Tracking shows delivered."


async def test_same_args_later_in_the_thread_is_a_new_request(client, izin, action, approver):
  graph = build_graph(izin, action, InMemorySaver(), [])
  config = thread_config()

  await graph.ainvoke(ARGS, config)
  [first] = await pending_request_ids(graph, config)
  await post_decision(client, {"id": first}, approver)
  await izin.resume_when_decided(graph, config)

  # Days later, same thread: an identical refund for a different order line.
  await graph.ainvoke(ARGS, config)
  [second] = await pending_request_ids(graph, config)
  assert second != first  # thread_id alone would have reused the first approval
  assert await requests_in_thread(config) == 2

def test_run_id_needs_a_thread():
  with pytest.raises(ValueError, match="thread_id"):
    run_id_from({"configurable": {}, "metadata": {"langgraph_step": 1, "langgraph_node": "n"}})