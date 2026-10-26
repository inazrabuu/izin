import asyncio
import uuid
from typing import TypedDict

from izin.langgraph import LangGraphApprovals, pending_request_ids
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
  order_id: str
  amount_idr: int
  customer: dict
  outcome: str

izin = LangGraphApprovals(url="http://localhost:8000", token="dev-token", agent_name="lg-refund-bot")

async def approve_and_refund(state: State, config: RunnableConfig) -> State:
  decision = await izin.in_node(config).require(
    "refund_order",
    args={k: state[k] for k in ("order_id", "amount_idr", "customer")},
    rationale="Courier tracking shows delivery failure."
  )
  return {"outcome": "refunded" if decision.approved else f"denied: {decision.comment}"}

async def main() -> None:
  builder = StateGraph(State)
  builder.add_node("approve_and_refund", approve_and_refund)
  builder.add_edge(START, "approve_and_refund")
  builder.add_edge("approve_and_refund", END)
  graph = builder.compile(checkpointer=InMemorySaver())

  config = {"configurable": {"thread_id": f"demo-{uuid.uuid4().hex[:8]}"}}
  await graph.ainvoke(
    {"order_id": "ORD-2002", "amount_idr": 750_000, "customer": {"name": "Budi H."}}, config
  )
  [rid] = await pending_request_ids(graph, config)
  print(f"graph paused... waiting on request {rid}. Approve it now.")

  result = await izin.resume_when_decided(graph, config)
  print(f"graph finished: {result['outcome']}")
  await izin.aclose()

if __name__ == "__main__":
  asyncio.run(main())