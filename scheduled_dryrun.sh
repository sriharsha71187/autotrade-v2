#!/bin/zsh
# scheduled_dryrun.sh — one-shot, launchd-invoked autonomous dry-run check.
# Args: $1 = launchd label (to self-disable after running), $2 = tag (e.g. 0935ET)
# Layer 1 (guaranteed): run the bot's dry-run and capture output.
# Layer 2 (best-effort): hand the result to a local headless `claude` agent that
#   reads the log, fixes clear bugs, commits them, and writes a report.
# It NEVER bootstraps the live job, places real orders, or flips PAPER.

export HOME="/Users/nirvaan"
export PATH="/Users/nirvaan/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LABEL="${1:-com.autotrade.dryrun}"
TAG="${2:-run}"
PY="$HOME/autotrade/venv/bin/python3"
BOT="$HOME/autotrade/autotrade.py"
CAP="$HOME/autotrade_dryrun_capture.log"
REPORT="$HOME/autotrade_dryrun_report.md"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"

cd "$HOME/autotrade" || exit 1

# --- Layer 1: guaranteed dry-run capture (places NO orders) ---
{
  echo "===== DRY-RUN $TAG @ $STAMP ====="
  "$PY" "$BOT" cycle --dry-run 2>&1
  echo "----- status -----"
  "$PY" "$BOT" status 2>&1
  echo "===== end $TAG ====="
} >> "$CAP" 2>&1

# --- Layer 2: best-effort autonomous analysis + safe fixes ---
PROMPT="You are doing an unattended dry-run check of the autotrade paper-trading bot in ~/autotrade (a local git repo). A scheduled dry-run just ran; its output is appended to ~/autotrade_dryrun_capture.log and the bot also logs to ~/autotrade.log. Steps: (1) Read the latest run in ~/autotrade_dryrun_capture.log and the matching tail of ~/autotrade.log. (2) You may run '~/autotrade/venv/bin/python3 ~/autotrade/autotrade.py cycle --dry-run' again yourself (it places NO orders). (3) Confirm whether the live path executed: market open?, signal scan built, option_chains fetched, a Claude decision returned, guardrails/execution ran with no exception. Flag any of: invalid/hallucinated OCC option symbols, OptionLatestQuote failures, short-bracket orientation errors, multi-leg spread issues, FATAL/traceback. (4) If you find a CLEAR, SAFE bug, fix it in code and 'git commit' it (one commit per fix, descriptive message). Run a quick py_compile after edits. (5) HARD RULES: do NOT run 'launchctl bootstrap' or start com.autotrade.cycle, do NOT place real orders, do NOT change PAPER=True, do NOT push to any remote. (6) Append a concise timestamped section to ~/autotrade_dryrun_report.md describing market state, whether the live path worked, anything suspicious, and any commits you made (include short SHAs). Keep it factual."

if command -v claude >/dev/null 2>&1; then
  echo "\n## Autonomous agent run $TAG @ $STAMP" >> "$REPORT"
  claude -p "$PROMPT" --dangerously-skip-permissions >> "$REPORT" 2>> "$CAP" \
    || echo "(headless claude agent failed — see $CAP; dry-run capture above is still valid)" >> "$REPORT"
else
  echo "\n## $TAG @ $STAMP — claude CLI not found; dry-run captured to $CAP only." >> "$REPORT"
fi

# --- one-shot: disable this job so it doesn't repeat ---
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
exit 0
