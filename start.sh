#!/usr/bin/env bash
set -e

python server.py &
PID_DISCORD=$!

cd telegram && python server.py &
PID_TELEGRAM=$!

trap "kill $PID_DISCORD $PID_TELEGRAM 2>/dev/null; exit" SIGTERM SIGINT

wait -n $PID_DISCORD $PID_TELEGRAM
EXIT_CODE=$?

kill $PID_DISCORD $PID_TELEGRAM 2>/dev/null
exit $EXIT_CODE
