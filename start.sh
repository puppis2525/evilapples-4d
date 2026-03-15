#!/usr/bin/env bash

echo "[start.sh] Starting Evil Apples 4D services..."

echo "[start.sh] Launching Discord server (port 5000)..."
python server.py &
PID_DISCORD=$!
echo "[start.sh] Discord server PID: $PID_DISCORD"

echo "[start.sh] Launching Telegram server (port 5001)..."
(cd telegram && PORT=5001 exec python server.py) &
PID_TELEGRAM=$!
echo "[start.sh] Telegram server PID: $PID_TELEGRAM"

cleanup() {
    echo "[start.sh] Shutting down services..."
    kill $PID_DISCORD $PID_TELEGRAM 2>/dev/null
    wait $PID_DISCORD 2>/dev/null
    wait $PID_TELEGRAM 2>/dev/null
    echo "[start.sh] All services stopped."
}

trap "cleanup; exit 1" SIGTERM SIGINT

echo "[start.sh] Both services launched. Waiting..."
wait -n $PID_DISCORD $PID_TELEGRAM 2>/dev/null
EXIT_CODE=$?
echo "[start.sh] A service exited with code $EXIT_CODE"

cleanup
exit $EXIT_CODE
