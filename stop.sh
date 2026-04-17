#!/usr/bin/env bash
# Stop the FastAPI app (uvicorn) and Azurite started by start.sh.

stopped=0

for pattern in "uvicorn backend.main:app" "azurite-table"; do
  pids=$(pgrep -f "$pattern" || true)
  if [ -n "$pids" ]; then
    echo "■ Stopping: $pattern (pids: $pids)"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    stopped=1
  fi
done

if [ "$stopped" = "0" ]; then
  echo "Nothing to stop — no uvicorn or azurite processes found."
fi
