import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict

import asyncpg

from izin_server.config import settings

log = logging.getLogger("izin.listener")
CHANNEL = "izin_decided"
HEALTH_CHECK_SECONDS = 10

class DecisionListener:
  def __init__(self) -> None:
    self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
    self._task: asyncio.Task | None = None
    self.connected = False

  def subscribe(self, request_id: uuid.UUID) -> asyncio.Event:
    event = asyncio.Event()
    self._waiters[str(request_id)].add(event)
    return event

  def unsubscribe(self, request_id: uuid.UUID, event: asyncio.Event) -> None:
    key = str(request_id)
    waiters = self._waiters.get(key)
    if waiters is not None:
      waiters.discard(event)
      if not waiters:
        del self._waiters[key]

  def _on_notify(self, connection, pid, channel, payload: str) -> None:
    for event in self._waiters.get(payload, ()):
      event.set()

  def _wake_all(self) -> None:
    for events in self._waiters.values():
      for event in events:
        event.set()

  async def _run(self) -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    backoff = 1.0
    while True:
      conn = True
      try:
        conn = await asyncpg.connect(dsn)
        await conn.add_listener(CHANNEL, self._on_notify)
        self.connected, backoff = True, 1.0
        log.info("listening on %s", CHANNEL)
        while True:
          await asyncio.sleep(HEALTH_CHECK_SECONDS)
          await conn.execute("SELECT 1")
      except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
        log.warning("decision listener lost its connection; reconnecting", exc_info=True)
      finally:
        self.connected = False
        self._wake_all()
        if conn is not None:
          conn.terminate()
      await asyncio.sleep(backoff)
      backoff = min(backoff * 2, 30.0)

  def start(self) -> None:
    if self._task is None:
      self._task = asyncio.create_task(self._run(), name="izin-decision-listener")

  async def stop(self) -> None:
    if self._task is not None:
      self._task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await self._task
      self._task = None

listener = DecisionListener()