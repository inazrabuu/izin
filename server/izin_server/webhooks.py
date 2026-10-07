import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

SIGNATURE_VERSION = "v1"
TOLERANCE_SECONDS = 300

def encode_body(payload: dict[str, Any]) -> bytes:
  return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()

def _mac(secret: str, delivery_id: str, timestamp: int, body: bytes) -> str:
  message = f"{delivery_id}.{timestamp}.".encode() + body
  return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

def sign(
  secret: str, delivery_id: str, body: bytes, timestamp: int | None = None
) -> dict[str, str]:
  ts = int(time.time()) if timestamp is None else timestamp
  return {
    "Content-Type": "application/json",
    "Izin-Delivery-Id": delivery_id,
    "Izin-Timestamp": str(ts),
    "Izin-Signature": f"{SIGNATURE_VERSION}={_mac(secret, delivery_id, ts, body)}"
  }

def verify(
  secret: str, headers: Mapping[str, str], body: bytes, now: float | None = None
) -> bool:
  try:
    delivery_id = headers["Izin-Delivery-Id"]
    ts = int(headers["Izin-Timestamp"])
    version, _, signature = headers["Izin-Signature"].partition("=")
  except (KeyError, ValueError):
    return False
  if version != SIGNATURE_VERSION:
    return False
  current = time.time() if now is None else now
  if abs(current - ts) > TOLERANCE_SECONDS:
    return False
  return hmac.compare_digest(_mac(secret, delivery_id, ts, body), signature)