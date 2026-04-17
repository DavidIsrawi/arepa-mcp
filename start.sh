#!/usr/bin/env bash
# Start Azurite (Table) and the FastAPI app together.
# Azurite runs in the background; the app runs in the foreground.
# Ctrl+C stops the app; Azurite is then cleaned up automatically.

set -e
cd "$(dirname "$0")"

# Start Azurite (Table) in the background
mkdir -p .azurite
echo "▶ Starting Azurite (Table) on :10002…"
azurite-table --tableHost 0.0.0.0 --location .azurite --silent &
AZURITE_PID=$!

cleanup() {
  echo
  echo "■ Stopping Azurite (pid $AZURITE_PID)…"
  kill "$AZURITE_PID" 2>/dev/null || true
  wait "$AZURITE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for Azurite port to accept connections
for _ in $(seq 1 30); do
  if (echo > /dev/tcp/127.0.0.1/10002) 2>/dev/null; then
    break
  fi
  sleep 0.2
done

echo "▶ Starting FastAPI app on :8000…"
ENV_ARG=()
if [ -f .env ]; then
  ENV_ARG=(--env-file .env)
fi
exec uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 "${ENV_ARG[@]}"
