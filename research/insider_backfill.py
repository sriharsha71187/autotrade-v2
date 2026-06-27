#!/usr/bin/env python3
"""Insider-transaction history from SEC EDGAR Form 4 (free, full history, point-in-time).
The documented signal is open-market BUYING (code P), not the routine option-exercise sells.
Per symbol per week: net insider buy $ (purchases - sales), # buys, # sells, # filings.

SEC requires a descriptive User-Agent and ~10 req/s. Scoped to the options/news universe so
it joins the cross-layer panel; resumable (skips done names). Output:
  ~/autotrade_insider_history/<SYMBOL>.jsonl  (weekly: net_buy, n_buy, n_sell, n_filings)
Run: python research/insider_backfill.py [--full] [SYM ...]
"""
import sys, json, time, re, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

OPT = Path.home() / "autotrade_options_history"
OUT = Path.home() / "autotrade_insider_history"
UA = {"User-Agent": "autotrade research nirvaan@example.com"}
START = "2023-01-01"


def _get(url, json_=False):
    req = urllib.request.Request(url, headers=UA)
    for _ in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=25).read()
            return json.loads(raw) if json_ else raw.decode("utf-8", "ignore")
        except Exception:
            time.sleep(0.5)
    return None


_CIK = {}
def cik_map():
    if _CIK: return _CIK
    d = _get("https://www.sec.gov/files/company_tickers.json", json_=True) or {}
    for v in d.values():
        _CIK[v["ticker"].upper().replace(".", "-")] = str(v["cik_str"]).zfill(10)
    return _CIK


def _txt(node, tag):
    e = node.find(f".//{tag}/value")
    if e is None: e = node.find(f".//{tag}")
    return e.text if e is not None and e.text else None


def parse_form4(xml):
    """Return (date, net_buy_value, n_buy, n_sell) from a Form 4 ownership XML."""
    try:
        root = ET.fromstring(re.sub(r'xmlns="[^"]+"', "", xml, count=1))  # strip default ns
    except Exception:
        return None
    date = _txt(root, "periodOfReport")
    buy, sell, nb, ns = 0.0, 0.0, 0, 0
    for tr in root.iter("nonDerivativeTransaction"):
        code = _txt(tr, "transactionCode")
        try:
            sh = float(_txt(tr, "transactionShares") or 0); pr = float(_txt(tr, "transactionPricePerShare") or 0)
        except (TypeError, ValueError):
            sh = pr = 0
        if code == "P": buy += sh * pr; nb += 1
        elif code == "S": sell += sh * pr; ns += 1
    return (date, buy - sell, nb, ns) if date else None


def backfill_symbol(sym, cik):
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik}.json", json_=True)
    if not sub: return None
    rec = sub.get("filings", {}).get("recent", {})
    forms, dates, accs, docs = rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", []), rec.get("primaryDocument", [])
    rows = []
    for form, fd, acc, doc in zip(forms, dates, accs, docs):
        if form != "4" or fd < START: continue
        accn = acc.replace("-", "")
        doc = doc.split("/")[-1]               # primaryDocument is the xsl-HTML render; raw XML is the bare filename
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}"
        xml = _get(url)
        time.sleep(0.12)                       # ~8 req/s, under SEC's 10/s
        if not xml or "<ownershipDocument" not in xml: continue
        p = parse_form4(xml)
        if p: rows.append(p)
    if not rows: return []
    df = pd.DataFrame(rows, columns=["date", "net", "nb", "ns"])
    df["date"] = pd.to_datetime(df["date"])
    df["wk"] = df["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    g = df.groupby("wk").agg(net_buy=("net", "sum"), n_buy=("nb", "sum"),
                             n_sell=("ns", "sum"), n_filings=("net", "size")).reset_index()
    return [{"date": r["wk"].date().isoformat(), "net_buy": round(float(r["net_buy"]), 0),
             "n_buy": int(r["n_buy"]), "n_sell": int(r["n_sell"]), "n_filings": int(r["n_filings"])}
            for _, r in g.iterrows()]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        syms = args
    elif "--full" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent)); import daily_universe_capture as c; syms = c.universe()
    else:
        syms = sorted(f.stem for f in OPT.glob("*.jsonl"))
    cm = cik_map(); OUT.mkdir(exist_ok=True)
    print(f"insider backfill: {len(syms)} symbols (CIK map: {len(cm)})")
    for i, s in enumerate(syms):
        if (OUT / f"{s}.jsonl").exists(): continue
        cik = cm.get(s.upper())
        if not cik:
            (OUT / f"{s}.jsonl").write_text(""); continue          # ETF / no CIK
        rows = backfill_symbol(s, cik)
        if rows is None:
            print(f"  {s}: fetch failed"); continue
        (OUT / f"{s}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
        nbuys = sum(r["n_buy"] for r in rows)
        print(f"  [{i+1}/{len(syms)}] {s}: {len(rows)} weeks, {nbuys} buy-txns")
    print("insider backfill done ->", OUT)


if __name__ == "__main__":
    main()
