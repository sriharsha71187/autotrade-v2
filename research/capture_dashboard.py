#!/usr/bin/env python3
"""Local dashboard for the nightly research capture — how the data collection is panning out.
Serves on http://localhost:8788  (autotrade=8787, sectorscope=8799). Recomputes on each load.

Run: python research/capture_dashboard.py   (then open the URL)
"""
import json, datetime as dt
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

CAP = Path.home() / "autotrade_universe_capture"
RET = Path.home() / "autotrade_universe_returns"
PORT = 8788
ETFS = {"SPY","QQQ","IWM","DIA","MDY","RSP","VTI","XLK","XLF","XLE","XLV","XLI","XLY","XLP",
        "XLU","XLB","XLRE","XLC","TLT","IEF","SHY","HYG","LQD","TIP","AGG","BND","GLD","SLV",
        "USO","GDX","DBC","UUP","VXX","SMH","SOXX","XBI","ARKK","KRE","ITB","JETS"}

FIELDS = [  # (label, predicate on a row)
    ("price",     lambda r: r.get("last") is not None),
    ("analyst",   lambda r: (r.get("analyst") or {}).get("n_analysts")),
    ("estimates", lambda r: (r.get("estimates") or {}).get("fwd_eps") is not None),
    ("dispersion",lambda r: (r.get("dispersion") or {}).get("eps_dispersion") is not None),
    ("earn surp", lambda r: (r.get("earnings") or {}).get("surprise_pct") is not None),
    ("short int", lambda r: (r.get("short") or {}).get("shares_short") is not None),
    ("insider",   lambda r: bool(r.get("insider"))),
    ("options",   lambda r: (r.get("options") or {}).get("atm_iv") is not None),
    ("social",    lambda r: (r.get("social") or {}).get("watchlist_count")),
    ("news",      lambda r: bool(r.get("news"))),
]


def load_day(f):
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def bar(pct, color="#3b82f6"):
    return (f'<div style="background:#1e293b;border-radius:3px;width:120px;height:14px;display:inline-block;'
            f'vertical-align:middle"><div style="background:{color};width:{pct:.0f}%;height:14px;'
            f'border-radius:3px"></div></div> <span style="color:#94a3b8">{pct:.0f}%</span>')


def build():
    days = sorted(CAP.glob("*.jsonl"))
    if not days:
        return "<h2>No capture files yet.</h2>"
    rows_html, totals = [], {lbl: 0 for lbl, _ in FIELDS}
    grand = 0
    spy_iv = []
    for f in days:
        rs = load_day(f); n = len(rs); grand += n
        netf = sum(1 for r in rs if r.get("symbol") in ETFS)
        cov = {lbl: sum(1 for r in rs if pred(r)) for lbl, pred in FIELDS}
        for lbl in totals: totals[lbl] += cov[lbl]
        s = next((r for r in rs if r.get("symbol") == "SPY"), None)
        if s and (s.get("options") or {}).get("atm_iv"):
            spy_iv.append((f.stem, s["options"]["atm_iv"]))
        rows_html.append(
            f"<tr><td><b>{f.stem}</b></td><td align=right>{n}</td><td align=right>{netf}</td>"
            + "".join(f'<td>{bar(100*cov[lbl]/n if n else 0)}</td>' for lbl, _ in FIELDS)
            + "</tr>")
    # forward-return maturity
    mat = {"1d": 0, "3d": 0, "5d": 0, "10d": 0, "20d": 0, "tot": 0}
    if RET.exists():
        for f in sorted(RET.glob("*.jsonl")):
            for l in f.read_text().splitlines():
                if not l.strip(): continue
                r = json.loads(l); mat["tot"] += 1
                for h in ("1d", "3d", "5d", "10d", "20d"):
                    if r.get(f"fwd_{h}") is not None: mat[h] += 1
    th = "".join(f"<th>{lbl}</th>" for lbl, _ in FIELDS)
    avg = "".join(f'<td>{bar(100*totals[lbl]/grand if grand else 0, "#22c55e")}</td>' for lbl, _ in FIELDS)
    ivrow = " · ".join(f"{d.split('-')[1]}/{d.split('-')[2]}: {iv*100:.0f}%" for d, iv in spy_iv[-10:])
    matrow = " · ".join(f"{h}: {mat[h]}" for h in ("1d","3d","5d","10d","20d")) if mat["tot"] else "none yet"
    return f"""
    <h1>📊 Research Capture — collection health</h1>
    <p class=sub>{len(days)} days · {grand:,} name-day observations · universe incl. {len(ETFS)} ETFs ·
    refreshed {dt.datetime.now().strftime('%H:%M:%S')}</p>

    <h3>Per-day coverage</h3>
    <table><tr><th>date</th><th>names</th><th>ETFs</th>{th}</tr>
    {''.join(rows_html)}
    <tr style="border-top:2px solid #334155"><td><b>avg</b></td><td></td><td></td>{avg}</tr>
    </table>

    <h3>Forward-return maturity <span class=sub>(when Tier-0 can run — needs horizons matured)</span></h3>
    <p>{matrow}{'' if mat['tot'] else ' — returns join not run yet (run mature_universe_returns.py)'}</p>

    <h3>SPY ATM IV (market fear, last 10 days)</h3>
    <p>{ivrow or 'n/a'}</p>
    """


HTML = """<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=60>
<title>Capture Dashboard</title><style>
body{{background:#0f172a;color:#e2e8f0;font:14px -apple-system,Segoe UI,Arial;margin:24px;max-width:1100px}}
h1{{margin:0 0 2px}} .sub{{color:#94a3b8;font-size:12px}} h3{{margin:22px 0 8px;color:#cbd5e1}}
table{{border-collapse:collapse;width:100%}} td,th{{padding:5px 8px;border-bottom:1px solid #1e293b;text-align:left}}
th{{color:#94a3b8;font-weight:600;font-size:12px}} td[align=right]{{text-align:right}}
</style></head><body>{body}</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try: body = build()
        except Exception as e: body = f"<pre>error: {e}</pre>"
        out = HTML.format(body=body).encode()
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass


if __name__ == "__main__":
    print(f"capture dashboard -> http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
