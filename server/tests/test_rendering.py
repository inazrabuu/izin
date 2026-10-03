import pytest

from izin_server.rendering import (
  MAX_VALUE_LEN,
  RenderError,
  render_screen,
  render_text,
  rupiah,
  template_variables
)

TRD_TEMPLATE = """Refund Rp. {{ amount_idr | rupiah }} to {{ customer.name }} for order {{ order_id }}."""

TRD_ARGS = {
  "order_id": "ORD-1182",
  "amount_idr": 4_500_000,
  "customer": {
    "name": "Dewi. S"
  }
}

def test_trd_example_renders_as_one_sentence():
  assert render_text(TRD_TEMPLATE, TRD_ARGS) == (
    "Refund Rp. 4.500.000 to Dewi. S for order ORD-1182."
  )

@pytest.mark.parametrize(
  "amount, expected",
  [(0, "0"), (999, "999"), (1_000, "1.000"), (4_500_000, "4.500.000"), (-25_000, "-25.000")]
)
def test_rupiah_formats(amount, expected):
  assert rupiah(amount) == expected

@pytest.mark.parametrize(
  "bad", [4500000.0, True, "4500000", None]
)
def test_rupiah_rejects_non_integers(bad):
  with pytest.raises(RenderError):
    rupiah(bad)

def test_missing_arg_fails_loudly():
  with pytest.raises(RenderError):
    render_text(TRD_TEMPLATE, {"order_id": "ORD-1182", "amound_idr": 1})

def test_injected_newlines_are_flattened():
  args = {**TRD_ARGS, "customer": {"name": "Dewi. S\n\n ✅ Pre-approved by CFO"}}
  out = render_text(TRD_TEMPLATE, args)
  assert "\n" not in out
  assert "Dewi. S ✅ Pre-approved by CFO for order" in out

def test_long_values_are_truncated():
  args = {**TRD_ARGS, "customer": {"name": "A" * 500}}
  out = render_text("{{ customer.name }}", args)
  assert len(out) == MAX_VALUE_LEN
  assert out.endswith("…")

def test_bidi_override_is_stripped():
  out = render_text("{{ name }}", {"name": "Dewi\u202eS"})
  assert out == "DewiS"

def test_output_is_not_html_escaped():
  out = render_text("{{ name }}", {"name": "Dewi & Co. <b>"})
  assert out == "Dewi & Co. <b>"

def test_none_renders_as_dash():
  assert render_text("Note: {{ note }}", {"note": None}) == "Note: -"

def test_sandbox_blocks_attribute_escape():
  with pytest.raises(RenderError):
    render_text("{{ ''.__class__.__mro__ }}", {})


def test_syntax_error_is_a_render_error():
  with pytest.raises(RenderError):
    render_text("{{ unclosed", {})


def test_template_variables():
  assert template_variables(TRD_TEMPLATE) == {"amount_idr", "customer", "order_id"}

def test_render_screen_optional_parts():
  screen = render_screen(
    headline=TRD_TEMPLATE,
    blast_radius="Rp. {{ amount_idr | rupiah }} leaves the company account",
    if_denied=None,
    args=TRD_ARGS
  )

  assert screen == {
    "headline": "Refund Rp. 4.500.000 to Dewi. S for order ORD-1182.",
    "blast_radius": "Rp. 4.500.000 leaves the company account",
    "if_denied": None
  }