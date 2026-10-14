import contextlib
import asyncio
import functools
import inspect
import logging
from collections import Counter
from typing import Any

import httpx

from izin.canonical import args_hash, canonicalize, idempotency_key
from izin.errors import (
  ApprovalTimeout, AuthError, ConcurrentDuplicate, Denied, IdempotencyConflict, InvalidRequest,
  IzinError, IzinUnavailable
)
from izin.models import Decision, Pending

log = logging.getLogger("izin")

class Approvals:
  def __init__(
    self, 
    *,
    url: str,
    token: str,
    agent_run_id: str,
    agent_name: str | None = None,
    http: httpx.AsyncClient | None = None,
    poll_wait: float = 30.0,
    max_attempts: int = 5
  ) -> None:
    if not agent_run_id or not str(agent_run_id).strip():
      raise ValueError(
        "agent_run_id is required and must be stable across restarts: "
        "it is how a restarted agent finds its earlier requests"
      )
    self.agent_run_id = str(agent_run_id)
    self.agent_name = agent_name
    self._headers = {"Authorization": f"Bearer {token}"}
    self._owns_http = http is None
    self._http = http or httpx.AsyncClient(base_url=url, timeout=poll_wait + 15)
    self._poll_wait = poll_wait
    self._max_attempts = max_attempts
    self._completed: Counter[tuple[str, str]] = Counter()
    self._in_flight: set[tuple[str, str]] = set()

  async def aclose(self) -> None:
    if self._owns_http:
      await self._http.aclose()

  async def __aenter__(self) -> "Approvals":
    return self

  async def __aexit__(self, *exc: object) -> None:
    await self.aclose()

  async def request(
    self,
    action: str,
    args: dict[str, Any],
    *,
    rationale: str | None = None,
    context: dict[str, Any] | None = None,
    max_wait: float | None = None
  ) -> Decision:
    canonical, slot = self._prepare(action, args)
    with self._in_flight_slot(slot):
      created = await self._create(action, canonical, self._key(slot), rationale, context or {})
      view = await self.wait(created["id"], max_wait=max_wait)
      return await self._complete(slot, view)

  async def check(
    self,
    action: str,
    args: dict[str, Any],
    *,
    rationale: str | None = None,
    context: dict[str, Any] | None = None
  ):
    canonical, slot = self._prepare(action, args)
    with self._in_flight_slot(slot):
      created = await self._create(action, canonical, self._key(slot), rationale, context or {})
      view = await self._fetch(created["id"], wait=0)
      if view["decision"] is None:
        return Pending(request_id=created["id"], headline=created["rendered"]["headline"])
      return await self._complete(slot, view)

  async def wait(self, request_id: str, *, max_wait: float | None = None) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = None if max_wait is None else loop.time() + max_wait
    while True:
      wait = self._poll_wait
      if deadline is not None:
        wait = max(0.0, min(wait, deadline - loop.time()))
      view = await self._fetch(request_id, wait=wait, retry_forever=True)
      if view["decision"] is not None:
        return view
      if deadline is not None and loop.time() >= deadline:
        raise ApprovalTimeout(request_id)

  def _prepare(self, action: str, args: dict[str, Any]):
    canonical = canonicalize(args)
    return canonical, (action, args_hash(canonical))

  def _key(self, slot: tuple[str, str]) -> str:
    return idempotency_key(self.agent_run_id, slot[0], slot[1], self._completed[slot])

  @contextlib.contextmanager
  def _in_flight_slot(self, slot: tuple[str, str]):
    if slot in self._in_flight:
      raise ConcurrentDuplicate(
        f"an identical {slot[0]!r} request is already in flight in this run; "
        "await it first, or make the args differ"
      )
    self._in_flight.add(slot)
    try:
      yield
    finally:
      self._in_flight.discard(slot)

  async def _complete(self, slot: tuple[str, str], view: dict[str, Any]) -> Decision:
    await self._consume(view["request_id"])
    self._completed[slot] += 1
    return Decision.from_view(view)

  async def _create(self, action, args, key, rationale, context) -> dict[str, Any]:
    r = await self._send(
      "POST",
      "/v1/requests",
      json={
        "action": action,
        "idempotency_key": key,
        "agent_run_id": self.agent_run_id,
        "agent_name": self.agent_name,
        "args": args,
        "rationale": rationale,
        "context": context
      }
    )
    if r.status_code in (200, 201):
      return r.json()
    raise _error_for(r)

  async def _fetch(
    self, request_id: str, *, wait: float, retry_forever: bool = False
  ) -> dict[str, Any]:
    r = await self._send(
      "GET", f"/v1/requests/{request_id}/decision",
      params={"wait": wait}, retry_forever=retry_forever
    )
    if r.status_code != 200:
      raise _error_for(r)
    view = r.json()
    if view["decision"] is None and view["status"] != "PENDING":
      raise IzinError(f"request {request_id} ended as {view['status']} without a decision")
    return view

  async def _consume(self, request_id: str) -> None:
    r = await self._send("POST", f"/v1/requests/{request_id}/consume")
    if r.status_code != 200:
      raise _error_for(r)

  async def _send(
    self, method: str, path: str, *, retry_forever: bool = False, **kwargs: Any
  ) -> httpx.Response:
    delay, attempt = 0.5, 0
    while True:
      attempt += 1
      try:
        r = await self._http.request(method, path, headers=self._headers, **kwargs)
        if r.status_code < 500 and r.status_code != 429:
          return r
        problem = f"HTTP {r.status_code}"
      except httpx.TransportError as e:
        problem = f"{type(e).__name__}: {e}"
      if not retry_forever and attempt >= self._max_attempts:
        raise IzinUnavailable(f"{method} {path} failed after {attempt} attempts ({problem})")
      log.warning("izin: %s %s failed (%s); retrying is %.1fs", method, path, problem, delay)
      await asyncio.sleep(delay)
      delay = min(delay * 2, 10.0)

  def requires(self, action: str, *, rationale: str | None = None):
    def decorate(fn):
      if not inspect.iscoroutinefunction(fn):
        raise TypeError("@requires wraps async functions")
      signature = inspect.signature(fn)

      @functools.wraps(fn)
      async def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        decision = await self.request(action, dict(bound.arguments), rationale=rationale)
        if not decision.approved:
          raise Denied(decision)
        return await fn(**decision.args)

      return wrapper
    return decorate

def _error_for(r: httpx.Response) -> IzinError:
  try:
    message = r.json()["error"]["message"]
  except (ValueError, KeyError, TypeError):
    message = r.text
  if r.status_code == 401:
    return AuthError(message)
  if r.status_code == 409:
    return IdempotencyConflict(message)
  if r.status_code in (400, 404, 422):
    return InvalidRequest(message)
  return IzinError(f"HTTP {r.status_code}: {message}")