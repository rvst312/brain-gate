#!/usr/bin/env bash
# Caso de uso 3 · Servicio HTTP privado para integraciones en otros lenguajes.
# Arranca la API con un token de prueba, enmascara un texto y prueba un rechazo.
set -euo pipefail
cd "$(dirname "$0")/.."

TOKEN="demo-token-$(date +%s)"
HASH=$(printf '%s' "$TOKEN" | shasum -a 256 | cut -d' ' -f1)
export BRAIN_GATE_INTEGRATIONS="{\"demo\": {\"token_sha256\": \"$HASH\", \"policies\": [\"brain-external-es\"]}}"

LOG=$(mktemp -t braingate-demo)
# PYTHONPATH=src evita depender del .pth editable (macOS puede ignorarlo, ver README).
PYTHONPATH=src uv run python -m brain_gate.cli serve --port 8088 >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do
  curl -sf localhost:8088/readyz >/dev/null && break
  kill -0 $PID 2>/dev/null || { echo "el servicio no arrancó:"; tail -20 "$LOG"; exit 1; }
  sleep 0.5
done
curl -sf localhost:8088/readyz >/dev/null || { echo "timeout esperando /readyz"; tail -20 "$LOG"; exit 1; }
curl -s localhost:8088/readyz; echo

echo "--- texto enmascarado"
curl -s localhost:8088/v1/mask -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"text": "María López pidió que le escribieran a maria@example.com", "policy_id": "brain-external-es", "include_spans": true}'
echo
echo "--- política no autorizada (403)"
curl -s localhost:8088/v1/mask -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"text": "hola", "policy_id": "otra"}'
echo
