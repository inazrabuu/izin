from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Decision:
  request_id: str
  verdict: str
  args: dict[str, Any]
  comment: str
  decided_by: str
  decided_at: str

  @property
  def approved(self) -> bool:
    return self.verdict in ("approve", "modify")

  @property
  def idempotency_key(self) -> str:
    return self.request_id

  @classmethod
  def from_view(cls, view: dict[str, Any]) -> "Decision":
    d = view["decision"]
    return cls(
      request_id=view["request_id"],
      verdict=d["verdict"],
      args=d["args"],
      comment=d["comment"],
      decided_by=d["principal_name"],
      decided_at=d["decided_at"]
    )