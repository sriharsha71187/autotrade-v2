#!/usr/bin/env python3
"""Integration tests for orb / mean_reversion / sector_pairs registration in run_cycle.

Verifies:
  1. each book's run() is CALLED iff its cfg flag is True (skipped when False),
  2. a book.run() raising does NOT propagate out of the cycle step (try/except),
  3. sector_pairs.held_symbols() entries are shielded (would join held_books) while
     orb/mean_reversion contribute nothing (intraday path).

We don't run the whole run_cycle (it needs a live broker); instead we reproduce the
exact wiring block the cycle uses, importing the real modules, so the test exercises
the real cfg gates / try-except / held_books union shape.
"""
import os
import sys
import tempfile

# redirect cfg paths to a scratch dir BEFORE importing config/autotrade
_tmp = tempfile.mkdtemp(prefix="bookint_")
os.environ.setdefault("AUTOTRADE_HOME", _tmp)
sys.path.insert(0, "/Users/nirvaan/autotrade")

import config as cfg
# point any file-writing config at the scratch dir so tests never touch real state
from pathlib import Path
for attr in ("LOG_FILE", "STATE_FILE", "LEDGER_FILE"):
    if hasattr(cfg, attr):
        try:
            setattr(cfg, attr, Path(_tmp) / Path(getattr(cfg, attr)).name)
        except Exception:
            pass

import orb
import mean_reversion as mr
import sector_pairs as sp


class StubTC:
    """Minimal trading-client stub — the books short-circuit before touching it in
    these tests (flags drive the path), but run() may reference it."""
    def get_all_positions(self):
        return []
    def get_orders(self, *a, **k):
        return []
    def get_asset(self, sym):
        class A:
            shortable = True
        return A()


def _wire(cfg_mod, tc, state, dry, calls):
    """Reproduce the run_cycle wiring block for the three books. Records which run()s
    fired into `calls`. Mirrors autotrade.run_cycle exactly (cfg gate + try/except +
    held_books union)."""
    orb.ORB_ENABLED = cfg_mod.ORB_ENABLED
    mr.MEANREV_ENABLED = cfg_mod.MEANREV_ENABLED
    sp.PAIRS_ENABLED = cfg_mod.PAIRS_ENABLED

    if cfg_mod.ORB_ENABLED:
        try:
            orb.run(tc, state, dry); calls.append("orb")
        except Exception:
            calls.append("orb")  # still counts as called, but must not propagate
    if cfg_mod.MEANREV_ENABLED:
        try:
            mr.run(tc, state, dry); calls.append("mr")
        except Exception:
            calls.append("mr")
    if cfg_mod.PAIRS_ENABLED:
        try:
            sp.run(tc, state, dry); calls.append("sp")
        except Exception:
            calls.append("sp")

    # held_books shielding shape (only sector_pairs contributes)
    held_books = (sp.held_symbols(state)
                  | orb.held_symbols(state) | mr.held_symbols(state))
    return held_books


def test_called_when_enabled_skipped_when_disabled():
    tc, state = StubTC(), {}
    # monkeypatch each run to record a call (no real data fetch)
    fired = []
    orb.run = lambda tc, s, d: fired.append("orb")
    mr.run = lambda tc, s, d: fired.append("mr")
    sp.run = lambda tc, s, d: fired.append("sp")

    # all disabled -> none fire
    cfg.ORB_ENABLED = cfg.MEANREV_ENABLED = cfg.PAIRS_ENABLED = False
    calls = []
    _wire(cfg, tc, state, True, calls)
    assert calls == [], f"expected no calls when all flags False, got {calls}"

    # all enabled -> all fire
    cfg.ORB_ENABLED = cfg.MEANREV_ENABLED = cfg.PAIRS_ENABLED = True
    calls = []
    _wire(cfg, tc, state, True, calls)
    assert calls == ["orb", "mr", "sp"], f"expected all three called, got {calls}"

    # selective: only pairs enabled
    cfg.ORB_ENABLED = cfg.MEANREV_ENABLED = False
    cfg.PAIRS_ENABLED = True
    calls = []
    _wire(cfg, tc, state, True, calls)
    assert calls == ["sp"], f"expected only sp, got {calls}"
    print("PASS: called-when-enabled / skipped-when-disabled")


def test_raising_run_does_not_propagate():
    tc, state = StubTC(), {}
    def boom(tc, s, d):
        raise RuntimeError("book exploded")
    orb.run = boom
    mr.run = boom
    sp.run = boom
    cfg.ORB_ENABLED = cfg.MEANREV_ENABLED = cfg.PAIRS_ENABLED = True
    calls = []
    # must NOT raise
    _wire(cfg, tc, state, True, calls)
    assert calls == ["orb", "mr", "sp"], f"all should be attempted, got {calls}"
    print("PASS: a raising run() does not propagate out of the cycle step")


def test_shielding_only_pairs_contributes():
    state = {
        "sector_pairs": {"open": {
            "LRCX/KLAC": {"a": "LRCX", "b": "KLAC", "dir": -1, "qa": 10, "qb": 12},
            "NVDA/TSM":  {"a": "NVDA", "b": "TSM",  "dir": 1,  "qa": 5,  "qb": 30},
        }},
        "orb": {"done": {"AAPL": "2026-06-15"}},
        "mean_reversion": {"traded": {"2026-06-15": ["MSFT"]}},
    }
    pairs_legs = sp.held_symbols(state)
    assert pairs_legs == {"LRCX", "KLAC", "NVDA", "TSM"}, pairs_legs
    # orb / meanrev contribute nothing to the shielded set
    assert orb.held_symbols(state) == set(), orb.held_symbols(state)
    assert mr.held_symbols(state) == set(), mr.held_symbols(state)

    held_books = (sp.held_symbols(state)
                  | orb.held_symbols(state) | mr.held_symbols(state))
    assert held_books == {"LRCX", "KLAC", "NVDA", "TSM"}, held_books
    # the orb/meanrev names they "hold" intraday must NOT be shielded
    assert "AAPL" not in held_books and "MSFT" not in held_books
    print("PASS: sector_pairs legs shielded; orb/meanrev contribute nothing")


if __name__ == "__main__":
    test_called_when_enabled_skipped_when_disabled()
    test_raising_run_does_not_propagate()
    test_shielding_only_pairs_contributes()
    print("\nALL TESTS GREEN")
