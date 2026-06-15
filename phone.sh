#!/bin/bash
# phone.sh — start a Claude Code session you can drive from the phone.
# Run on the MAC (Terminal), then open the Claude mobile app and tap "AutoTrade".
#   bash ~/autotrade/phone.sh
#
# cd ~/autotrade            : conversations are per-folder; this one lives here,
#                             so --continue only finds it from inside this dir
# --remote-control AutoTrade  : registers this session so the phone app can attach
# --continue                  : resumes your latest conversation (this one)
# --permission-mode bypassPermissions : no permission prompts (must be set HERE,
#                             the phone can't elevate it after connecting)
cd ~/autotrade || exit 1
exec claude --remote-control AutoTrade --continue --permission-mode bypassPermissions
