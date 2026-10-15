import asyncio
import time
import uuid
from contextlib import suppress

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from helpers import ARGS, post_decision, HEADLINE
from izin import Approvals, ApprovalTimeout, ConcurrentDuplicate, Denied, Decision, Pending
from izin_server.db import SessionLocal
from izin_server.models import Request, RequestStatus

def new_run() -> str:
  return f"run-{uuid.uuid4().hex}"

def approvals_for(http, run_id) -> Approvals:
  return Approvals(url="http://test", token="test-token", agent_run_id=run_id, http=http)

async def request_ids(run_id: str) -> list[uuid.UUID]:
  async with SessionLocal() as s:
    rows = await s.scalars(
      select(Request.id).where(Request.agent_run_id == run_id).order_by(Request.created_at)
    )
    return list(rows)

async def wait_for_request(run_id: str, n: int = 1, task: asyncio.Task | None = None) -> uuid.UUID:
  for _ in range(100):
    if task is not None and task.done():
      task.result()
      raise AssertionError("The agent finished without creating a request")
    ids = await request_ids(run_id)
    if len(ids) >= n:
      return ids[n - 1]
    await asyncio.sleep(0.05)
  raise AssertionError("the request never appeared")

async def status_of(rid: uuid.UUID) -> RequestStatus:
  async with SessionLocal() as s:
    return await s.scalar(select(Request.status).where(Request.id == rid))

async def test_request_waits_for_a_human(client, sdk_http, action, approver):
  run_id = new_run()
  task = asyncio.create_task(
    approvals_for(sdk_http, run_id).request(action, ARGS, rationale="Courier lost it.")
  )
  rid = await wait_for_request(run_id, task=task)
  await post_decision(client, {"id": str(rid)}, approver)

  decision = await asyncio.wait_for(task, timeout=5)
  assert decision.approved
  assert decision.args == ARGS
  assert decision.decided_by == "Ana"
  assert decision.request_id == str(rid)
  assert await status_of(rid) == RequestStatus.CONSUMED

async def test_resumes_after_the_process_dies(client, sdk_http, action, approver):
  run_id = new_run()
  first_life = asyncio.create_task(approvals_for(sdk_http, run_id).request(action, ARGS))
  rid = await wait_for_request(run_id, task=first_life)

  first_life.cancel()
  with suppress(asyncio.CancelledError):
    await first_life

  await post_decision(client, {"id": str(rid)}, approver)

  t0 = time.monotonic()
  decision = await approvals_for(sdk_http, run_id).request(action, ARGS)
  assert decision.approved
  assert decision.request_id == str(rid)
  assert time.monotonic() - t0 < 1
  assert len(await request_ids(run_id)) == 1

async def test_resume_matches_equivalent_args(client, sdk_http, action, approver):
  run_id = new_run()
  first_life = asyncio.create_task(approvals_for(sdk_http, run_id).request(action, ARGS))
  rid = await wait_for_request(run_id, task=first_life)
  first_life.cancel()
  with suppress(asyncio.CancelledError):
    await first_life
  await post_decision(client, {"id": str(rid)}, approver)

  reordered = {"customer": {"name": "Dewi S."}, "amount_idr": 4500000.0, "order_id": "ORD-1182"}
  decision = await approvals_for(sdk_http, run_id).request(action, reordered)
  assert decision.request_id == str(rid)

async def test_identical_calls_in_one_run_are_separate_requests(client, sdk_http, action, approver):
  run_id = new_run()
  approvals = approvals_for(sdk_http, run_id)
  for n in (1, 2):
    task = asyncio.create_task(approvals.request(action, ARGS))
    rid = await wait_for_request(run_id, n, task=task)
    await post_decision(client, {"id": str(rid)}, approver)
    await asyncio.wait_for(task, timeout=5)
  assert len(await request_ids(run_id)) == 2


async def test_max_wait_raises_but_the_request_survives(client, sdk_http, action, approver):
  approvals = approvals_for(sdk_http, new_run())
  with pytest.raises(ApprovalTimeout) as exc:
    await approvals.request(action, ARGS, max_wait=1)

  await post_decision(client, {"id": exc.value.request_id}, approver)
  decision = await approvals.request(action, ARGS)  
  assert decision.request_id == exc.value.request_id


async def test_identical_concurrent_calls_are_refused(sdk_http, action):
  approvals = approvals_for(sdk_http, new_run())
  first = asyncio.create_task(approvals.request(action, ARGS))
  await asyncio.sleep(0.1)
  with pytest.raises(ConcurrentDuplicate):
    await approvals.request(action, ARGS)
  first.cancel()
  with suppress(asyncio.CancelledError):
    await first


async def test_decorator_runs_only_after_approval(client, sdk_http, action, approver):
  run_id = new_run()
  approvals = approvals_for(sdk_http, run_id)
  calls = []

  @approvals.requires(action)
  async def refund(order_id: str, amount_idr: int, customer: dict) -> str:
    calls.append(amount_idr)
    return "refunded"

  task = asyncio.create_task(refund(**ARGS))
  rid = await wait_for_request(run_id, task=task)
  assert calls == []  # nothing ran before a human said yes
  await post_decision(client, {"id": str(rid)}, approver)

  assert await asyncio.wait_for(task, timeout=5) == "refunded"
  assert calls == [4_500_000]

async def test_decorator_raises_on_denial(client, sdk_http, action, approver):
  run_id = new_run()
  approvals = approvals_for(sdk_http, run_id)

  @approvals.requires(action)
  async def refund(order_id: str, amount_idr: int, customer: dict) -> str:
    raise AssertionError("must not run")

  task = asyncio.create_task(refund(**ARGS))
  rid = await wait_for_request(run_id, task=task)
  await post_decision(
    client, {"id": str(rid)}, approver, verdict="deny", comment="Tracking shows delivered."
  )

  with pytest.raises(Denied) as exc:
    await asyncio.wait_for(task, timeout=5)
  assert exc.value.decision.comment == "Tracking shows delivered."

async def test_context_manager_closes_cleanly(sdk_http):
  async with approvals_for(sdk_http, new_run()) as approvals:
    assert approvals.agent_run_id.startswith("run-")

async def test_check_does_not_block(client, sdk_http, action, approver):
  approvals = approvals_for(sdk_http, new_run())

  first = await approvals.check(action, ARGS)
  assert isinstance(first, Pending)
  assert first.headline == HEADLINE

  await post_decision(client, {"id": first.request_id}, approver)
  second = await approvals.check(action, ARGS)
  assert isinstance(second, Decision)
  assert second.request_id == first.request_id