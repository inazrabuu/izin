import asyncio
import logging
import random
import signal
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.db import SessionLocal, engine
from izin_server.models import Outbox, OutboxStatus, RequestEvent
from izin_server.webhooks import encode_body, sign

log = logging.getLogger("izin.worker")

LEASE_SECONDS = 60
MAX_ATTEMPTS = 8
BASE_BACKOFF = 5.0
MAX_BACKOFF = 3600.0
IDLE_SLEEP = 1.0

@dataclass(frozen=True)
class Claimed:
  id: int
  request_id: uuid.UUID
  channel: str
  payload: dict[str, Any]
  attempts: int

@dataclass(frozen=True)
class Outcome:
  ok: bool
  permanent: bool = False
  error: str | None = None

async def claim(session: AsyncSession, batch_size: int) -> list[Claimed]:
  due = (
    select(Outbox.id)
    .where(Outbox.status == OutboxStatus.PENDING, Outbox.next_attempt_at <= func.now())
    .order_by(Outbox.next_attempt_at)
    .limit(batch_size)
    .with_for_update(skip_locked=True)
  )
  rows = (
    await session.execute(
      update(Outbox)
      .where(Outbox.id.in_(due))
      .values(
        attempts=Outbox.attempts + 1,
        next_attemps_at=func.now() + timedelta(seconds=LEASE_SECONDS),
      )
      .returning(
        Outbox.id, Outbox.request_id, Outbox.channel, Outbox.payload, Outbox.attempts
      )
      .execution_options(synchronize_session=False)
    )
  ).all()

  return [Claimed(**row._mapping) for row in rows]

async def deliver(
  http: httpx.AsyncClient, item: Claimed, url: str | None, secret: str
) -> Outcome:
  if item.channel != "webhook":
    return Outcome(ok=False, permanent=True, error=f"no adapter for channel {item.channel!r}")
  if not url:
    return Outcome(ok=False, permanent=True, error="webhook_url is not configured")

  body = encode_body(item.payload)
  try:
    r = await http.post(url, content=body, headers=sign(secret, str(item.id), body))
  except httpx.HTTPError as e:
    return Outcome(ok=False, error=f"{type(e).__name__}: {e}")

  if r.is_success:
    return Outcome(ok=True)

  permanent = 400 <= r.status_code < 500 and r.status_code not in (408, 429)
  return Outcome(ok=False, permanent=permanent, error=f"HTTP {r.status_code}")

def backoff_seconds(attempts: int) -> float:
  base = min(BASE_BACKOFF * 2 ** (attempts - 1), MAX_BACKOFF)
  return base * random.uniform(0.8, 1.2)

async def record_outcome(item: Claimed, outcome: Outcome) -> None:
  if outcome.ok:
    values: dict[str, Any] = {"status": OutboxStatus.SENT, "last_error": None}
    event = "notified"
  elif outcome.permanent or item.attempts >= MAX_ATTEMPTS:
    values = {"status": OutboxStatus.FAILED, "last_error": outcome.error}
    event = "notify_failed"
  else:
    delay = backoff_seconds(item.attempts)
    values = {
      "next_attempt_at": func.now() + timedelta(seconds=delay),
      "last_error": outcome.error
    }
    event = None

  async with SessionLocal() as session:
    result = await session.execute(
      update(Outbox)
      .where(
        Outbox.id == item.id,
        Outbox.attempts == item.attempts,
        Outbox.status == OutboxStatus.PENDING,
      )
      .values(**values)
      .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
      log.warning("outbox %s: lease lost; discarding this outcome", item.id)
      return
    if event:
      await session.execute(
        insert(RequestEvent).values(
          request_id=item.request_id,
          type=event,
          payload={
            "channel": item.channel,
            "delivery_id": str(item.id),
            "error": outcome.error
          }
        )
      )
    await session.commit()

  if outcome.ok:
    log.info("outbox %s delivered", item.id)
  elif event:
    log.error("outbox %s failed permanently: %s", item.id, outcome.error)
  else:
    log.warning(
      "outbox %s attempt %s failed (%s); will retry", item.id, item.attempts, outcome.error
    )

async def run_once(
  http: httpx.AsyncClient, *, url: str | None, secret: str, batch_size: int = 10
) -> int:
  async with SessionLocal() as session:
    claimed = await claim(session, batch_size)
    await session.commit()

  async def process(item: Claimed) -> None:
    await record_outcome(item, await deliver(http, item, url, secret))

  await asyncio.gather(*(process(item) for item in claimed))
  return len(claimed)

async def main() -> None:
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
  stop = asyncio.Event()
  loop = asyncio.get_running_loop()
  for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(sig, stop.set)

  log.info("worker started; webhook_url=%s", settings.webhook_url or "(not configured)")
  async with httpx.AsyncClient(timeout=10.0) as http:
    while not stop.is_set():
      try:
        n = await run_once(
          http, url=settings.webhook_url, secret=settings.webhook_secret
        )
      except Exception:
        log.exception("worker iteration failed")
        n = 0
      if n == 0:
        try:
          await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP)
        except TimeoutError:
          pass

  await engine.dispose()
  log.info("worker stopped")

if __name__ == "__main__":
  asyncio.run(main())