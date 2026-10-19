#!/usr/bin/env bash
# Runs both backend servers with live, request-level logging.
#   - HTTP  :8000  frame uploads
#   - HTTPS :8443  live agent (Realtime), report, static web UI
# Logs stream to the terminal AND to logs/*.log. Ctrl-C stops both.

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs data/sweeps

PY=./.venv/bin/python
export DATA_DIR=./data/sweeps

echo "▶ starting HTTP  :8000  (uploads)        → logs/http.log"
$PY -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --log-level info \
  2>&1 | tee logs/http.log &
HTTP_PID=$!

echo "▶ starting HTTPS :8443  (agent/report)   → logs/https.log"
$PY -m uvicorn backend.app:app --host 0.0.0.0 --port 8443 --log-level info \
  --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem \
  2>&1 | tee logs/https.log &
HTTPS_PID=$!

trap 'echo; echo "stopping…"; kill $HTTP_PID $HTTPS_PID 2>/dev/null || true' INT TERM
echo "✓ both running. Ctrl-C to stop. Mac IP: $(ipconfig getifaddr en0 2>/dev/null || echo '?')"
wait
