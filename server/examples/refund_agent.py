"""A tiny agent that needs approval before refunding.

Run from server/:  uv run python examples/refund_agent.py <run-id>
Kill it with Ctrl-C while it waits, approve, then run it again with the same run id.
"""
import asyncio
import sys

from izin import Approvals


async def main(run_id: str) -> None:
  async with Approvals(
    url="http://localhost:8000", token="dev-token",
    agent_run_id=run_id, agent_name="refund-bot"
  ) as approvals:
    print(f"[{run_id}] asking for approval...")
    decision = await approvals.request(
      "refund_order",
      {"order_id": "ORD-1185", "amount_idr": 4_500_000, "customer": {"name": "Dewi S."}},
      rationale="Customer reported item never arrived, courrier tracking "
                "shows delivery failure on 2026-09-09"
    )
    if decision.approved:
      amount = decision.args["amount_idr"]
      print(f"[{run_id}] approved by {decision.decided_by} -> refunding {amount} "
            f"(downstream idempotency key: {decision.idempotency_key})")
      if decision.modified:
        for field, change in decision.changes.items():
          print(f"[{run_id}] {field} changed: {change['from']:,} -> {change['to']:,}")
        print(f"[{run_id}] reason: {decision.comment}")
    else:
      print(f"[{run_id}] denied by {decision.decided_by}: {decision.comment}")

if __name__ == "__main__":
  asyncio.run(main(sys.argv[1]))