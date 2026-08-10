#!/bin/bash
cd "$(dirname "$0")"
python3 app.py &
SERVER_PID=$!
sleep 2
open http://localhost:5001
echo ""
echo "TCG logger running at http://localhost:5001"
echo "Close this window or press Ctrl+C to stop."
wait $SERVER_PID
