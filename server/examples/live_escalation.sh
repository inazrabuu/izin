#!/usr/bin/env bash
# Live check for Chunk 2a: two 60s steps -> escalated -> expired -> decided.
set -euo pipefail

cd "$(dirname "$0")/../.."                      # repo root, where docker-compose.yml lives
[ -f .env ] && { set -a; source .env; set +a; }

API="${IZIN_PUBLIC_URL:-http://localhost:8000}"
AGENT="Authorization: Bearer ${IZIN_API_TOKEN:-dev-token}"
ADMIN="Authorization: Bearer ${IZIN_ADMIN_TOKEN:-dev-admin-token}"
ACTION="live_chain_$(date +%s)"

echo "1. PUT action $ACTION (two 60s steps)"
curl -sf -X PUT "$API/v1/actions/$ACTION" -H "$ADMIN" -H "Content-Type: application/json" -d '{
  "display_name": "Live escalation check",
  "risk_level": "medium",
  "args_schema": {"type": "object", "required": ["note"], "properties": {"note": {"type": "string"}}},
  "render_template": "Live check: {{ note }}",
  "steps": [
    {"role": "approver", "after": 60},
    {"role": "approver", "after": 60}
  ]
}' | jq '{name, on_timeout, steps}'

echo "2. POST request"
RID=$(curl -sf -X POST "$API/v1/requests" -H "$AGENT" -H "Content-Type: application/json" -d "{
  \"action\": \"$ACTION\",
  \"idempotency_key\": \"$(openssl rand -hex 32)\",
  \"agent_run_id\": \"live-$(date +%s)\",
  \"args\": {\"note\": \"escalation chain\"},
  \"rationale\": \"Chunk 2a live check.\"
}" | jq -r .id)
echo "   request $RID"

echo "   waiting 150s for step 0 -> step 1 -> timeout..."
sleep 150

echo "3. Events for $RID"
docker compose exec -T db psql -U izin -d izin -c \
  "SELECT type, payload, at FROM request_events WHERE request_id = '$RID' ORDER BY id"

docker compose exec -T db psql -U izin -d izin -c \
  "SELECT step_index, role, due_at FROM routes WHERE request_id = '$RID' ORDER BY step_index"