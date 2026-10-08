#!/usr/bin/env bash
# Make Nirvaan Chess Central always-on — like a website, no terminal needed.
#
#   ./install-always-on.sh        # install: starts now, auto-starts at login,
#                                 # restarts if it crashes, reachable on home wifi
#   ./install-always-on.sh off    # uninstall the auto-start
#
# After installing, every device on your wifi can open it — bookmark it or use
# Share → Add to Home Screen on the iPad for a real app icon.
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
LABEL="com.nirvaan.chess-central"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer is for macOS (it uses launchd). On other systems run ./run.sh"
  exit 1
fi

if [ "${1:-}" = "off" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Auto-start removed. (You can still run it manually with ./run.sh)"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR/data"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$APP_DIR/run.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key><string>0.0.0.0</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$APP_DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/data/server.log</string>
</dict>
</plist>
EOF

DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

# (re)load it — bootout first so re-running the installer picks up changes.
# bootout returns before launchd has finished tearing the old job down;
# bootstrapping during that window fails with "Load failed: 5: Input/output
# error" and leaves NOTHING running, so wait for it to be gone, then retry.
launchctl bootout "$SERVICE" 2>/dev/null || true
for _ in $(seq 1 20); do
  launchctl print "$SERVICE" >/dev/null 2>&1 || break
  sleep 0.5
done
loaded=0
for attempt in 1 2 3 4 5; do
  if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then loaded=1; break; fi
  if launchctl print "$SERVICE" >/dev/null 2>&1; then loaded=1; break; fi
  sleep "$attempt"
done
if [ "$loaded" != 1 ]; then
  echo "❌ macOS refused to start the background service. Try:"
  echo "   launchctl bootstrap $DOMAIN $PLIST"
  echo "   (or restart the Mac — it auto-starts at login)"
  exit 1
fi
launchctl enable "$SERVICE" 2>/dev/null || true
launchctl kickstart "$SERVICE" 2>/dev/null || true

# don't claim success until the app actually answers (first start may spend
# a minute installing Python packages)
printf "Starting"
up=0
for _ in $(seq 1 90); do
  if curl -fsS -m 2 http://127.0.0.1:8425/api/version >/dev/null 2>&1; then up=1; break; fi
  printf "."
  sleep 2
done
echo ""
if [ "$up" != 1 ]; then
  echo "❌ The service is installed but the app isn't answering yet."
  echo "   Last lines of data/server.log:"
  tail -n 25 "$APP_DIR/data/server.log" 2>/dev/null | sed 's/^/     /'
  exit 1
fi
BUILD="$(curl -fsS -m 2 http://127.0.0.1:8425/api/version 2>/dev/null || true)"

HOSTNAME_LOCAL="$(scutil --get LocalHostName 2>/dev/null || hostname -s).local"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")"

echo ""
echo "♞ Chess Central is now always on — it starts with this Mac and restarts itself."
echo "   Running version: $BUILD"
echo ""
echo "   On this Mac:        http://localhost:8425"
echo "   Any device on wifi: http://$HOSTNAME_LOCAL:8425"
[ -n "$LAN_IP" ] && \
echo "                       http://$LAN_IP:8425   (if the name doesn't work)"
echo ""
echo "   Nirvaan's page:     http://$HOSTNAME_LOCAL:8425/kid"
echo "   iPad tip: open it in Safari → Share → Add to Home Screen → a real app icon."
echo ""
echo "   Set a Parent PIN in Settings so the grown-up dashboard stays locked."
echo "   Logs: data/server.log · Turn off:  ./install-always-on.sh off"
