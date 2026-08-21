#!/usr/bin/env bash
# Nirvaan Chess Central — one-command start.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating virtualenv + installing dependencies…"
  # prefer a modern python if one is installed; macOS system python3 (3.9) also works
  PY=python3
  for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
  "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
fi
# keep dependencies in sync with requirements.txt on every start (fast no-op
# when current). Never fatal — an offline Mac must still serve the app.
./.venv/bin/pip install --quiet -r requirements.txt \
  || echo "⚠️  Could not refresh dependencies (offline?) — continuing with what's installed."

if [ ! -x ./stockfish-bin ] && ! command -v stockfish >/dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/stockfish ] && [ ! -x /usr/local/bin/stockfish ]; then
  echo "⚠️  Stockfish not found — install with:  brew install stockfish"
  echo "   (or drop a binary at ./stockfish-bin — see README)"
  echo "   The app still runs; analysis waits until the engine is available."
fi

# Default binds to this Mac only. To use kid mode on an iPad on your home
# wifi:  HOST=0.0.0.0 ./run.sh   — then open http://<this-mac's-ip>:8425/kid
# (set a Parent PIN in Settings first; the API has no other authentication)
HOST="${HOST:-127.0.0.1}"
echo "♞ Nirvaan Chess Central →  http://localhost:8425   (kid mode: /kid)"
if [ "$HOST" != "127.0.0.1" ]; then
  echo "   LAN mode: also reachable at http://$(ipconfig getifaddr en0 2>/dev/null || hostname):8425"
fi
exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port 8425 "$@"
