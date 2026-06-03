# AutoTrade — Rebuild v1

Clean single-engine rewrite. Replaces the 9-module setup with one `autotrade.py`.

## Files
- `autotrade.py` — the engine. Modes: `cycle`, `eod`, `status`.
- `config.py` — keys + paths + hard guardrails (edit guardrails here).
- `alpaca_system_prompt.txt` — the bot's trading instructions.
- `install.sh` — removes old launchd jobs, installs the single cycle job, installs deps.
- `com.autotrade.cycle.plist` — the 5-minute scheduler.
- `autotrade.env.template` — copy to `~/.autotrade.env` and fill in keys.

## First-time setup
```bash
# 1. put the folder in your home directory as ~/autotrade
# 2. create your secrets file
cp ~/autotrade/autotrade.env.template ~/.autotrade.env
nano ~/.autotrade.env          # paste your keys, save
chmod 600 ~/.autotrade.env

# 3. install (deps + scheduler, removes the old jobs)
bash ~/autotrade/install.sh
```

## Test BEFORE letting it trade on its own
```bash
# builds full context + asks Claude, but places NO orders:
~/autotrade/venv/bin/python3 ~/autotrade/autotrade.py cycle --dry-run

# check what it sees:
~/autotrade/venv/bin/python3 ~/autotrade/autotrade.py status
```
Read `~/autotrade.log`. When the dry-run decisions look sane, it's already live on
the 5-minute schedule (the installer loaded it). To pause it anytime:
```bash
launchctl bootout gui/$(id -u)/com.autotrade.cycle
```

## End-of-day learning
Not auto-scheduled yet (on purpose). After the close:
```bash
~/autotrade/venv/bin/python3 ~/autotrade/autotrade.py eod
```

## Remote control
- File fallback (works today): `echo "STOP" > ~/autotrade_command.txt`
  Commands: `STOP`, `RESUME`, `CLOSE ALL`, `STATUS`, `STATS`, `FOCUS TECH`.
- Telegram: add `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` to `~/.autotrade.env`. Then
  the same commands work from your phone and the bot DMs you on entries/exits/EOD.

## Bugs from the old version, and how they're fixed
1. NVDA fixation → stats shown neutrally; system prompt forbids historical-label
   trading; selection is scan-driven.
2. close_position double-action → single clean `close_symbols` path.
3. cancel_stale_orders killing bracket legs → that function does not exist here.
   Brackets are placed once and never touched.
4. Wrong stop/target in log → we log the bracket's actual prices, never the model's.
5. Double log entries → installer removes `com.alpacatrader.opening` permanently.
6. Signal scan missing from prompt → scan is built every cycle and always included
   in the context object sent to Claude.
