#!/usr/bin/env bash
set -euo pipefail

CORE_URL="${CORE_URL:-http://127.0.0.1:8000}"
GATEWAY_URL="${GATEWAY_URL:-}"
SERVICE_TOKEN="${DIT_SERVER_SERVICE_TOKEN:-${SERVICE_TOKEN:-}}"
CREATE_BOOTSTRAP_TOKEN="${CREATE_BOOTSTRAP_TOKEN:-0}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

request_status() {
  curl -fsS -o /dev/null -w "%{http_code}" "$@"
}

need curl
need python3

echo "Checking dit-core health at ${CORE_URL}/health"
health_json="$(curl -fsS "${CORE_URL}/health")"
python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
checks = payload.get("checks", {})
errors = []
if payload.get("status") != "healthy":
    errors.append(f"status={payload.get('status')!r}")
for name in ("database", "data_dir"):
    if checks.get(name, {}).get("status") != "healthy":
        errors.append(f"{name}={checks.get(name)}")
if errors:
    raise SystemExit("unhealthy core: " + ", ".join(errors))
print("core health: healthy")
PY

metrics_status="$(request_status "${CORE_URL}/metrics")"
if [ "$metrics_status" != "200" ]; then
  echo "metrics endpoint returned HTTP ${metrics_status}, expected 200" >&2
  exit 1
fi
echo "core metrics: reachable"

repos_status="$(request_status "${CORE_URL}/api/v1/repos")"
if [ "$repos_status" != "401" ]; then
  echo "unauthenticated /api/v1/repos returned HTTP ${repos_status}, expected 401" >&2
  exit 1
fi
echo "core auth guard: unauthenticated request rejected"

if [ "$CREATE_BOOTSTRAP_TOKEN" = "1" ]; then
  if [ -z "$SERVICE_TOKEN" ]; then
    echo "CREATE_BOOTSTRAP_TOKEN=1 requires DIT_SERVER_SERVICE_TOKEN or SERVICE_TOKEN" >&2
    exit 2
  fi
  echo "Checking service-token bootstrap path"
  token_json="$(curl -fsS -X POST "${CORE_URL}/api/v1/admin/tokens" \
    -H "Content-Type: application/json" \
    -H "X-Service-Token: ${SERVICE_TOKEN}" \
    -d '{"label":"deployment-smoke","permissions":"admin"}')"
  admin_token="$(python3 - "$token_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
token = payload.get("token")
if not isinstance(token, str) or not token.startswith("dit_"):
    raise SystemExit("bootstrap response did not include a dit_ token")
print(token)
PY
)"
  auth_status="$(request_status "${CORE_URL}/api/v1/repos" -H "Authorization: Bearer ${admin_token}")"
  if [ "$auth_status" != "200" ]; then
    echo "admin token /api/v1/repos returned HTTP ${auth_status}, expected 200" >&2
    exit 1
  fi
  echo "service-token bootstrap: created and verified admin token"
else
  echo "service-token bootstrap: skipped (set CREATE_BOOTSTRAP_TOKEN=1 to exercise it)"
fi

if [ -n "$GATEWAY_URL" ]; then
  echo "Checking gateway health at ${GATEWAY_URL}/api/healthz"
  gateway_status="$(request_status "${GATEWAY_URL}/api/healthz")"
  if [ "$gateway_status" != "200" ]; then
    echo "gateway health returned HTTP ${gateway_status}, expected 200" >&2
    exit 1
  fi
  echo "gateway health: reachable"
else
  echo "gateway health: skipped (set GATEWAY_URL to check it)"
fi

echo "deployment smoke checks passed"
