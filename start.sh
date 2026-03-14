#!/usr/bin/env bash

python server.py &
PID_DISCORD=$!

cd telegram && python server.py &
PID_TELEGRAM=$!

cleanup() {
    kill $PID_DISCORD $PID_TELEGRAM 2>/dev/null
    wait $PID_DISCORD 2>/dev/null
    wait $PID_TELEGRAM 2>/dev/null
}

trap "cleanup; exit 1" SIGTERM SIGINT

wait -n $PID_DISCORD $PID_TELEGRAM
EXIT_CODE=$?

cleanup
exit $EXIT_CODE
