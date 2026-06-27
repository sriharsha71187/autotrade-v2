#!/usr/bin/env python3
"""EODHD US Stock Options Data — historical EOD IV / greeks / OI (the one genuinely-paid
perishable layer). Reads EODHD_API_KEY from ~/.autotrade.env (never hard-coded).

First job (verify): confirm the endpoint works + the ACTUAL history depth available on this
subscription (the 1yr-vs-2.5yr question) before we build the backfiller on top of it.

Run: python research/eodhd_options.py verify        # connection + earliest date + sample fields
     python research/eodhd_options.py sample AAPL    # one symbol, recent rows
"""
import sys, json, urllib.request, urllib.parse
from pathlib import Path

ENV = Path.home() / ".autotrade.env"
BASE = "https://eodhd.com/api"


def _key():
    for ln in ENV.read_text().splitlines():
        if ln.startswith("EODHD_API_KEY") and "=" in ln:
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("EODHD_API_KEY not found in ~/.autotrade.env — add it as EODHD_API_KEY=...")


def _get(path, params):
    params = {**params, "api_token": _key(), "fmt": "json"}
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# EODHD has had two options endpoints; we probe both and use whichever the subscription serves.
def fetch_eod(symbol, date_from=None, date_to=None):
    """Historical EOD options chain (IV/greeks/OI) for `symbol`. Tries the Unicorn-Bay
    marketplace endpoint first, then the legacy options endpoint."""
    errs = []
    # 1) Unicorn Bay (current marketplace product)
    try:
        p = {"filter[underlying_symbol]": symbol}
        if date_from: p["filter[tradetime_from]"] = date_from
        if date_to:   p["filter[tradetime_to]"] = date_to
        d = _get("mp/unicornbay/options/eod", p)
        if d: return ("unicornbay", d)
    except Exception as e: errs.append(f"unicornbay: {str(e)[:80]}")
    # 2) Legacy options endpoint
    try:
        p = {}
        if date_from: p["from"] = date_from
        if date_to:   p["to"] = date_to
        d = _get(f"options/{symbol}.US", p)
        if d: return ("legacy", d)
    except Exception as e: errs.append(f"legacy: {str(e)[:80]}")
    raise SystemExit("both endpoints failed:\n  " + "\n  ".join(errs))


def fetch_day(symbol, date):
    """All option contracts for one underlying on one tradetime date (paginated)."""
    out, offset = [], 0
    while True:
        d = _get("mp/unicornbay/options/eod", {
            "filter[underlying_symbol]": symbol,
            "filter[tradetime_from]": date, "filter[tradetime_to]": date,
            "page[limit]": 1000, "page[offset]": offset})
        recs = d.get("data", [])
        out += [r["attributes"] for r in recs if r.get("attributes")]
        if len(recs) < 1000:
            break
        offset += 1000
    return out


def features(symbol, date):
    """Reduce one underlying-day's chain to perishable option-regime features.
    moneyness is (strike-spot)/spot-style: ~0 = ATM, <0 = OTM put strikes, >0 = OTM call strikes."""
    ch = fetch_day(symbol, date)
    ch = [c for c in ch if c.get("volatility") and c.get("moneyness") is not None
          and 0.02 < c["volatility"] < 3]                       # drop junk IVs
    if not ch:
        return {}
    calls = [c for c in ch if c["type"] == "call"]
    puts  = [c for c in ch if c["type"] == "put"]
    nt = lambda cs: [c for c in cs if 15 <= (c.get("dte") or 0) <= 60]   # ~1-2mo window
    def atm_iv(cs):                                              # ATM = |moneyness| smallest
        a = sorted(nt(cs), key=lambda c: abs(c["moneyness"]))[:3]
        return sum(c["volatility"] for c in a) / len(a) if a else None
    def iv_at(cs, m):                                            # IV near a given moneyness
        s = [c for c in nt(cs) if abs(c["moneyness"] - m) < 0.04]
        return sum(c["volatility"] for c in s) / len(s) if s else None
    coi  = sum(c.get("open_interest") or 0 for c in calls); poi = sum(c.get("open_interest") or 0 for c in puts)
    cvol = sum(c.get("volume") or 0 for c in calls);        pvol = sum(c.get("volume") or 0 for c in puts)
    gex = sum((c.get("gamma") or 0)*(c.get("open_interest") or 0)*(1 if c["type"]=="call" else -1) for c in ch)
    atm_c, atm_p = atm_iv(calls), atm_iv(puts)
    put_otm, call_otm = iv_at(puts, 0.1), iv_at(calls, -0.1)    # OTM put(+moneyness) vs OTM call(-moneyness)
    atm = round((atm_c+atm_p)/2, 4) if atm_c and atm_p else (atm_c or atm_p)
    return {"date": date, "symbol": symbol, "n_contracts": len(ch), "atm_iv": atm,
            "skew": round(put_otm - call_otm, 4) if put_otm and call_otm else None,   # +ve = downside fear
            "term_slope": round((iv_at(calls, 0) or 0) - (atm_c or 0), 4) if atm_c else None,
            "pc_oi": round(poi/coi, 3) if coi else None, "pc_vol": round(pvol/cvol, 3) if cvol else None,
            "total_oi": coi+poi, "total_vol": cvol+pvol, "gex_proxy": round(gex, 1)}


def verify():
    print("EODHD options — verifying connection + history depth …")
    src, d = fetch_eod("AAPL", date_from="2023-01-01", date_to="2023-12-31")
    # normalize to a list of records
    recs = d.get("data", d) if isinstance(d, dict) else d
    print(f"  endpoint: {src} | records (2023 probe): {len(recs) if hasattr(recs,'__len__') else '?'}")
    if recs:
        r0 = recs[0]
        fields = list(r0.keys()) if isinstance(r0, dict) else "?"
        print("  sample fields:", fields)
        # earliest date present tells us the real history depth
        dates = [r.get("tradetime") or r.get("date") or r.get("exp_date") for r in recs if isinstance(r, dict)]
        dates = sorted([x for x in dates if x])
        if dates:
            print(f"  earliest record in 2023 probe: {dates[0]}  -> history reaches at least this far")
    print("  (if 2023 returned data, the 2.5-yr depth is real; if empty, it's the ~1-yr version)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "verify":
        verify()
    elif cmd == "sample":
        src, d = fetch_eod(sys.argv[2])
        recs = d.get("data", d) if isinstance(d, dict) else d
        print(json.dumps(recs[:2] if hasattr(recs, "__getitem__") else recs, indent=2)[:1500])
