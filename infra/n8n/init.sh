#!/bin/sh
set -e
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
