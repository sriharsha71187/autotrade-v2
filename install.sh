#!/bin/bash
# install.sh — set up the rebuilt AutoTrade bot.
# Run once:  bash ~/autotrade/install.sh
set -e

HOME_DIR="$HOME"
DIR="$HOME_DIR/autotrade"
LA="$HOME_DIR/Library/LaunchAgents"
mkdir -p "$LA"

echo "==> Removing OLD scheduler jobs (kills the double-log bug for good)"
for OLD in com.alpacatrader.fivemin com.alpacatrader.opening com.autotrade.cycle; do
    PLIST="$LA/$OLD.plist"
    launchctl bootout "gui/$(id -u)/$OLD" 2>/dev/null || true
    launchctl unload "$PLIST" 2>/dev/null || true
    if [ -f "$PLIST" ]; then rm -f "$PLIST"; echo "    removed $OLD"; fi
done

echo "==> Installing the single cycle job (every 5 min)"
sed "s|__HOME__|$HOME_DIR|g" "$DIR/com.autotrade.cycle.plist" > "$LA/com.autotrade.cycle.plist"
launchctl bootstrap "gui/$(id -u)" "$LA/com.autotrade.cycle.plist"
echo "    loaded com.autotrade.cycle"

echo "==> Creating an isolated virtual environment (~/autotrade/venv)"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/python3" -m pip install --quiet --upgrade pip
echo "==> Installing dependencies into the venv"
# pip auto-selects the newest alpaca-py compatible with this Python (3.9 -> last 3.9 build).
# anthropic = decision model SDK (required); flask = optional local dashboard (dashboard.py).
"$DIR/venv/bin/python3" -m pip install --quiet alpaca-py yfinance requests anthropic flask || \
    echo "    (install failed — run manually: $DIR/venv/bin/python3 -m pip install alpaca-py yfinance requests anthropic flask)"
echo "    installed alpaca-py / yfinance / requests / anthropic / flask"

echo
echo "Done. EOD learning is NOT scheduled automatically — run it after the close with:"
echo "    python3 $DIR/autotrade.py eod"
echo "(or add a separate launchd job timed for ~4:10pm ET once you're happy with it)."
echo
echo "Before first run, confirm ~/.autotrade.env exists with your keys (chmod 600)."
echo "Test safely first:  python3 $DIR/autotrade.py cycle --dry-run"
