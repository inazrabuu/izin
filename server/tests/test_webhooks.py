from izin_server.webhooks import encode_body, sign, verify

SECRET = "s3cret"
BODY = encode_body({"b": 1, "a": "x"})

def test_signature_roundtrip():
  assert verify(SECRET, sign(SECRET, "42", BODY), BODY)

def test_tampering_is_detected():
  headers = sign(SECRET, "42", BODY)
  assert not verify(SECRET, headers, BODY + b" ")
  assert not verify("other-secret", headers, BODY)
  assert not verify(SECRET, {**headers, "Izin-Delivery-Id": "43"}, BODY)

def test_stale_timestamp_is_rejected():
  headers = sign(SECRET, "42", BODY, timestamp=1_000_000)
  assert not verify(SECRET, headers, BODY)
  assert verify(SECRET, headers, BODY, now=1_000_000)