import hashlib
import json
import math
import re
from typing import Any

from izin.errors import NonCanonicalValue, SecretInArgs

_SECRET_PATTERNS = [
  re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),          # sk-style API keys
  re.compile(r"\bAKIA[0-9A-Z]{16}\b"),             # AWS access key IDs
  re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),     # GitHub tokens
  re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),   # Slack tokens
  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

def canonicalize(value: Any, path: str = "args") -> Any:
  if value is None or isinstance(value, bool):
    return value
  if isinstance(value, str):
    if any(p.search(value) for p in _SECRET_PATTERNS):
      raise SecretInArgs(f"{path} looks like a credential, secrets must not enter args")
    return value
  if isinstance(value, int):
    return value
  if isinstance(value, float):
    if math.isnan(value) or math.isinf(value):
      raise NonCanonicalValue(f"{path}: NaN and infinity are not allowed")
    return int(value) if value.is_integer() else value
  if isinstance(value, dict):
    out = {}
    for k, v in value.items():
      if not isinstance(k, str):
        raise NonCanonicalValue(f"{path}: keys must be strings, got {type(k).__name__}")
      out[k] = canonicalize(v, f"{path}.{k}")
    return out
  if isinstance(value, (list, tuple)):
    return [canonicalize(v, f"{path}[{i}]") for i, v in enumerate(value)]
  raise NonCanonicalValue(
    f"{path}: {type(value).__name__} is not allowed in args. Convert it explicitly "
    f"(e.g. str(...)) so a retried call produces exactly the same value."
  )

def canonical_json(value: Any) -> bytes:
  return json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
  ).encode()

def args_hash(canonical_args: dict[str, Any]) -> str:
  return hashlib.sha256(canonical_json(canonical_args)).hexdigest()

def idempotency_key(agent_run_id: str, action: str, digest: str, sequence: int) -> str:
  return hashlib.sha256(canonical_json([agent_run_id, action, digest, sequence])).hexdigest()