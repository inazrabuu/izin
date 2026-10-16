import datetime
import decimal
import uuid

import pytest

from izin.canonical import args_hash, canonicalize, idempotency_key
from izin.errors import NonCanonicalValue, SecretInArgs

def test_key_order_does_not_matter():
  a = canonicalize({"order_id": "X", "customer": {"name": "D", "tier": 1}})
  b = canonicalize({"customer": {"tier": 1, "name": "D"}, "order_id": "X"})
  assert args_hash(a) == args_hash(b)

def test_integral_floats_become_ints():
  assert canonicalize({"amount": 4500000.0}) == {"amount": 4500000}
  assert args_hash(canonicalize({"amount": 4500000.0})) == args_hash({"amount": 4500000})

def test_fractional_floats_are_kept():
  assert canonicalize({"rate": 0.15}) == {"rate": 0.15}

@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nan_and_infinity_are_rejected(bad):
  with pytest.raises(NonCanonicalValue):
    canonicalize({"x": bad})

@pytest.mark.parametrize(
  "bad", [datetime.datetime.now(), uuid.uuid4(), decimal.Decimal("1.5"), {1, 2}]
)
def test_non_json_types_are_rejected_with_a_path(bad):
  with pytest.raises(NonCanonicalValue, match=r"args\.when"):
    canonicalize({"when": bad})

def test_non_string_keys_are_rejected():
  with pytest.raises(NonCanonicalValue):
    canonicalize({1: "x"})

def test_tuples_become_lists():
  assert canonicalize({"items": ("a", "b")}) == {"items": ["a", "b"]}

@pytest.mark.parametrize(
  "secret", 
  ["sk-" + "a" * 24, "AKIA" + "ABCDEFGHIJKLMNOP", "gh" + "p_" + "a" * 36],
  "izin_p_" + "a" * 43
)
def test_secrets_are_rejected_without_echoing_them(secret):
  with pytest.raises(SecretInArgs) as exc:
    canonicalize({"note": f"use {secret} please"})
  assert secret not in str(exc.value)

def test_key_is_sha256_hex_and_depends_on_sequence():
  k0 = idempotency_key("run-1", "refund_order", "abc", 0)
  k1 = idempotency_key("run-1", "refund_order", "abc", 1)
  assert len(k0) == 64 and all(c in "0123456789abcdef" for c in k0)
  assert k0 != k1

def test_key_has_no_delimiter_ambiguity():
  assert idempotency_key("a|b", "c", "h", 0) != idempotency_key("a", "b|c", "h", 0)