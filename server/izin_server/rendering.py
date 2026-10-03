import unicodedata
from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined, Template, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment

MAX_VALUE_LEN = 20

class RenderError(ValueError):
  """The template can't be rendered against these args. Maps to HTTP 422."""
  pass

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
    cat = unicodedata.category(ch)
    if cat == "Cc":
      chars.append(" ")
    elif cat == "Cf":
      continue
    else:
      chars.append(ch)

  text = " ".join("".join(chars).split())
  if len(text) > MAX_VALUE_LEN:
    text = text[:MAX_VALUE_LEN - 1] + "..."

  return text

_env = SandboxedEnvironment(
  undefined=StrictUndefined,
  autoescape=False,
  finalize=_clean
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

def render_text(source: str, args: dict[str, Any]) -> str:
  try:
    out = compile(source).render(args)
  except RenderError:
    raise
  except TemplateError as e:
    raise RenderError(f"{type(e).__name__}: {e}") from e
  except (TypeError, ValueError, ArithmeticError) as e:
    raise RenderError(f"{type(e).__name__}: {e}") from e

  return " ".join(out.split())

def render_screen(
  *,
  headline: str,
  blast_radius: str | None,
  if_denied: str | None,
  args: dict[str, Any],
) -> dict[str, str | None]:
  """The frozen screen stored in requests.rendered."""
  return {
    "headline": render_text(headline, args),
    "blast_radius": render_text(blast_radius, args) if blast_radius else None,
    "if_denied": render_text(if_denied, args) if if_denied else None
  }