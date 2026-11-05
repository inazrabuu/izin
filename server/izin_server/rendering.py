import unicodedata
from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined, Template, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

MAX_VALUE_LEN = 120
OPEN, CLOSE = "\ue000", "\ue001"

class RenderError(ValueError):
  """The template can't be rendered against these args. Maps to HTTP 422."""

def rupiah(value: Any) -> str:
  if isinstance(value, bool) or not isinstance(value, int):
    raise RenderError(f"rupiah expects an integer amount, got {type(value).__name__}")
  sign = "-" if value < 0 else ""
  return f"{sign}{abs(value):,}".replace(",", ".")

def _clean(value: Any) -> str:
  if value is None:
    return "-"
  
  text = str(value)
  chars = []
  for ch in text:
    if ch in (OPEN, CLOSE):
      continue

    cat = unicodedata.category(ch)
    if cat == "Cc":
      chars.append(" ")
    elif cat == "Cf":
      continue
    else:
      chars.append(ch)

  text = " ".join("".join(chars).split())
  if len(text) > MAX_VALUE_LEN:
    text = text[:MAX_VALUE_LEN - 1] + "\u2026"

  return text

def _mark(value: Any) -> str:
  return OPEN + _clean(value) + CLOSE

_env = SandboxedEnvironment(
  undefined=StrictUndefined,
  autoescape=False,
  finalize=_mark,
)
_env.filters["rupiah"] = rupiah

@lru_cache(maxsize=256)
def _compile(source: str) -> Template:
  return _env.from_string(source)

#----------------- Public API -----------------------
def template_variables(source: str) -> set[str]:
  try:
    return meta.find_undeclared_variables(_env.parse(source))
  except TemplateError as e:
    raise RenderError(f"{type(e).__name__}: {e}") from e

def _render_marked(source: str, args: dict[str, Any]) -> str:
  try:
    out = _compile(source).render(args)
  except RenderError:
    raise
  except TemplateError as e:
    raise RenderError(f"{type(e).__name__}: {e}") from e
  except (TypeError, ValueError, ArithmeticError) as e:
    raise RenderError(f"{type(e).__name__}: {e}") from e
  return " ".join(out.split())

def _plain(marked: str) -> str:
  return marked.replace(OPEN, "").replace(CLOSE, "")

def _segments(marked: str) -> list[dict[str, Any]]:
  segments: list[dict[str, Any]] = []
  buffer: list[str] = []
  agent = False
  for ch in marked:
    if ch in (OPEN, CLOSE):
      if buffer:
        segments.append({"text": "".join(buffer), "agent": agent})
        buffer = []
      agent = ch == OPEN
    else:
      buffer.append(ch)
  if buffer:
    segments.append({"text": "".join(buffer), "agent": agent})
  return segments

def render_text(source: str, args: dict[str, Any]) -> str:
  return _plain(_render_marked(source, args))

def render_screen(
  *,
  headline: str,
  blast_radius: str | None,
  if_denied: str | None,
  args: dict[str, Any],
) -> dict[str, Any]:
  screen: dict[str, Any] = {}
  segments: dict[str, list[dict[str, Any]]] = {}
  for key, source in (("headline", headline), ("blast_radius", blast_radius), ("if_denied", if_denied)):
    if source:
      marked = _render_marked(source, args)
      screen[key] = _plain(marked)
      segments[key] = _segments(marked)
    else:
      screen[key] = None
  screen["segments"] = segments
  return screen