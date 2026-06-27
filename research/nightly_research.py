#!/usr/bin/env python3
"""Nightly research job — the autonomous engine pass. Spawned detached by the loop daemon
once per day after the close (and safe to run by hand). Each night it:
  1. captures tonight's broad-universe point-in-time snapshot (perishable layer)
  2. matures forward returns for all prior captures
  3. re-runs the BACKFILLS that still have ground to cover (news/ratings/macro are quick;
     options is paid+slow so it's resumable and self-limits)
  4. re-runs every DISCOVERY sweep on the latest data and updates the strategy board

So the board + learnings advance on their own as data accumulates — no need to be in a
session. Each step is independent (one failing never blocks the rest). Run: python research/nightly_research.py
"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def step(label, fn):
    try:
        print(f"\n=== {label} ===")
        fn()
    except Exception:
        print(f"{label} FAILED:"); traceback.print_exc()


if __name__ == "__main__":
    import daily_universe_capture, mature_universe_returns
    import news_backfill, ratings_backfill, macro_backfill
    import discover, discover_xlayer, discover_setups

    # 1-2: capture + mature
    step("capture (perishable snapshot)", daily_universe_capture.main)
    step("mature forward returns", mature_universe_returns.main)
    # 3: keep the free historical backfills current (resumable; skip names already done)
    step("news backfill", news_backfill.main)
    step("ratings backfill", ratings_backfill.main)
    step("macro backfill", macro_backfill.main)
    # 4: re-run all discovery on the latest data -> updates the board
    step("discovery: single+interaction", discover.main)
    step("discovery: cross-layer", discover_xlayer.main)
    step("discovery: setups", discover_setups.main)
    print("\nnightly research pass complete.")
