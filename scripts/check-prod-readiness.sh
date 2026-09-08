#!/bin/sh
# DAILYOPS-OPS-3: production-readiness gate.
# Fails (exit 1) when DEPLOY_ENV=production but required secrets are missing.
# Usage: DEPLOY_ENV=production WEBHOOK_SECRET=... AGENT_API_KEY=... ./scripts/check-prod-readiness.sh
set -e

DEPLOY_ENV="${DEPLOY_ENV:-${N8N_DEPLOY_ENV:-dev}}"

if [ "$DEPLOY_ENV" != "production" ]; then
  echo "[prod-readiness] DEPLOY_ENV=$DEPLOY_ENV — skipping production checks."
  exit 0
fi

FAIL=0
for VAR in WEBHOOK_SECRET AGENT_API_KEY; do
  VAL=$(eval "echo \${$VAR:-}")
  if [ -z "$VAL" ]; then
    echo "[prod-readiness] FATAL: $VAR is empty in production mode."
    FAIL=1
  fi
done

if [ "$FAIL" = "1" ]; then
  echo "[prod-readiness] Refusing production deploy: set the missing secret(s) first."
  exit 1
fi

echo "[prod-readiness] OK: required production secrets are set."
