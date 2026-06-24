#!/usr/bin/env python3
"""Nightly research job: capture tonight's broad-universe point-in-time snapshot, then
mature forward returns for all prior captures. Spawned detached by the loop daemon once
per day after the close so it never blocks trading. Safe to run by hand too.

Run: python research/nightly_research.py
"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_universe_capture as capture
import mature_universe_returns as mature

if __name__ == "__main__":
    try:
        capture.main()
    except Exception:
        print("capture failed:"); traceback.print_exc()
    try:
        mature.main()
    except Exception:
        print("mature failed:"); traceback.print_exc()
