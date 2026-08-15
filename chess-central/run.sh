#!/usr/bin/env bash
# Nirvaan Chess Central — one-command start.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: creating virtualenv + installing dependencies…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if ! command -v stockfish >/dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/stockfish ] && [ ! -x /usr/local/bin/stockfish ]; then
  echo "⚠️  Stockfish not found — install with:  brew install stockfish"
  echo "   (the app still runs; deep analysis waits for the engine)"
fi

echo "♞ Nirvaan Chess Central →  http://localhost:8425   (kid mode: /kid)"
exec ./.venv/bin/uvicorn app.main:app --port 8425 "$@"
