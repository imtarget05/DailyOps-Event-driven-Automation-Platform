#!/bin/sh
# DAILYOPS-06 (R11): credential DEV/PROD separation. In production we MUST NOT
# import placeholder/dev credentials (localhost, example.com, password 'dailyops',
# REPLACE_WITH_*) into the live credential store. Fail hard instead.
set -e

N8N_DEPLOY_ENV="${N8N_DEPLOY_ENV:-dev}"
PLACEHOLDER_PATTERNS="localhost|127\.0\.0\.1|example\.com|REPLACE_WITH|password.*dailyops|dailyops-dev"

if [ "$N8N_DEPLOY_ENV" = "production" ]; then
  echo "[n8n-init] PRODUCTION credential preflight (DAILYOPS-06)..."
  for f in /workflows/*.json /creds/*.json; do
    [ -f "$f" ] || continue
    if grep -qEi "$PLACEHOLDER_PATTERNS" "$f" 2>/dev/null; then
      echo "[n8n-init] FATAL: $f contains placeholder/dev credential values (matches: $PLACEHOLDER_PATTERNS)."
      echo "  Refusing to import credentials into a PRODUCTION n8n instance."
      exit 1
    fi
  done
  # DAILYOPS-OPS-2: WEBHOOK_SECRET must be set in production — the approval-link
  # HMAC token and the Business Event webhook guard are meaningless without it.
  if [ -z "${WEBHOOK_SECRET:-}" ]; then
    echo "[n8n-init] FATAL: WEBHOOK_SECRET is empty in production. Set it before deploying."
    exit 1
  fi
  echo "[n8n-init] credential preflight OK."
fi

echo "[n8n-init] importing workflows..."
for f in /workflows/*.json; do
  echo "[n8n-init] importing $f"
  n8n import:workflow --input="$f"
done
echo "[n8n-init] activating (publishing) all workflows..."
for f in /workflows/*.json; do
  id=$(node -e "console.log(JSON.parse(require('fs').readFileSync('$f')).id)" 2>/dev/null || true)
  if [ -n "$id" ]; then
    n8n publish:workflow --id="$id" || echo "[n8n-init] publish failed for $id"
  else
    n8n update:workflow --all --active=true
    break
  fi
done
echo "[n8n-init] done."
