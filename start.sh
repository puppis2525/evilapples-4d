#!/usr/bin/env bash

echo "[start.sh] Starting Evil Apples 4D services..."

# Write Telegram PID to a file so the monitor can track restarts
TG_PID_FILE="/tmp/tg_server.pid"

start_telegram() {
    (cd telegram && PORT=5001 python server.py) &
    TG_PID=$!
    echo $TG_PID > "$TG_PID_FILE"
    echo "[start.sh] Telegram server started (PID: $TG_PID)"
}

echo "[start.sh] Launching Discord server (port 5000)..."
python server.py &
PID_DISCORD=$!
echo "[start.sh] Discord server PID: $PID_DISCORD"

echo "[start.sh] Launching Telegram server (port 5001)..."
start_telegram

cleanup() {
    echo "[start.sh] Shutting down services..."
    TG_PID=$(cat "$TG_PID_FILE" 2>/dev/null)
    kill $PID_DISCORD $TG_PID 2>/dev/null
    wait $PID_DISCORD 2>/dev/null
    wait $TG_PID 2>/dev/null
    echo "[start.sh] All services stopped."
}

trap "cleanup; exit 1" SIGTERM SIGINT

# Monitor the Telegram server; restart it on crash.
# Replit health-checks port 5000 (Discord), so Discord must stay alive
# regardless of the Telegram server's state.
telegram_monitor() {
    while true; do
        TG_PID=$(cat "$TG_PID_FILE" 2>/dev/null)
        if [ -n "$TG_PID" ] && ! kill -0 "$TG_PID" 2>/dev/null; then
            echo "[start.sh] Telegram server (PID: $TG_PID) is down, restarting in 5s..."
            sleep 5
            start_telegram
        fi
        sleep 10
    done
}
telegram_monitor &

echo "[start.sh] Both services launched. Waiting for Discord server..."
wait $PID_DISCORD
EXIT_CODE=$?
echo "[start.sh] Discord server exited with code $EXIT_CODE"

cleanup
exit $EXIT_CODE
