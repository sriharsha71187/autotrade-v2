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
# keep dependencies in sync with requirements.txt on every start (fast no-op when current)
./.venv/bin/pip install --quiet -r requirements.txt

if ! command -v stockfish >/dev/null 2>&1 \
   && [ ! -x /opt/homebrew/bin/stockfish ] && [ ! -x /usr/local/bin/stockfish ]; then
  echo "⚠️  Stockfish not found — install with:  brew install stockfish"
  echo "   (the app still runs; deep analysis waits for the engine)"
fi

echo "♞ Nirvaan Chess Central →  http://localhost:8425   (kid mode: /kid)"
exec ./.venv/bin/uvicorn app.main:app --port 8425 "$@"
