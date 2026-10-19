#!/usr/bin/env bash
# Create N pending refund requests against a running izin.
# Usage: examples/simulate.sh [count]     env: IZIN_URL, IZIN_API_TOKEN
set -euo pipefail

URL="${IZIN_URL:-http://localhost:8000}"
TOKEN="${IZIN_API_TOKEN:-dev-token}"
COUNT="${1:-3}"
NAMES=("Dewi S." "Budi H." "Sari W." "Agus P." "Rina K.")

for i in $(seq 1 "$COUNT"); do
  key="sim-$(date +%s)-$RANDOM-$i"                 # unique, so each call is a new request
  name="${NAMES[$(( (i - 1) % ${#NAMES[@]} ))]}"
  amount=$(( (RANDOM % 90 + 1) * 50000 ))          # Rp 50.000 to Rp 4.500.000

  curl -sf -X POST "$URL/v1/requests" \
    -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
    -d @- <<EOF | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["id"], "-", r["rendered"]["headline"])'
{"action": "refund_order", "idempotency_key": "$key", "agent_run_id": "$key",
 "agent_name": "simulator",
 "args": {"order_id": "ORD-$((1000 + RANDOM % 9000))", "amount_idr": $amount,
          "customer": {"name": "$name"}},
 "rationale": "Customer reported the parcel never arrived; courier tracking shows a failed delivery."}
EOF
done