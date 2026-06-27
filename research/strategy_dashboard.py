#!/usr/bin/env python3
"""Strategies board — the living ledger of candidate algorithms the research engine is
pursuing, their status + metrics, and the accumulating learnings. Reads
~/autotrade_strategies.json fresh on each load (I append to it as tests run).
Serves http://localhost:8790  (capture dash = 8788). Run: python research/strategy_dashboard.py
"""
import json, datetime as dt
from pathlib import Path
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

LEDGER = Path.home() / "autotrade_strategies.json"
PORT = 8790
SC = {"validated": "#22c55e", "candidate": "#3b82f6", "testing": "#eab308",
      "queued": "#64748b", "rejected": "#ef4444"}


def m(v, pct=False, dp=2):
    if v is None: return '<span style="color:#475569">–</span>'
    return f"{v*100:.0f}%" if pct else f"{v:.{dp}f}"


RESEARCH_JOBS = {
    "eodhd_backfill": "options history backfill", "news_backfill": "news history backfill",
    "discover_xlayer": "cross-layer discovery sweep", "discover_setups": "setup discovery sweep",
    "discover_options": "options discovery sweep", "discover": "feature discovery sweep",
    "option_trade_sim": "option-trade backtest", "trend_backtest": "trend backtest",
    "daily_universe_capture": "nightly perishable capture",
    "ratings_backfill": "analyst-ratings backfill", "macro_backfill": "macro/regime backfill",
}


def running_now():
    # auto-detect ANY research/*.py process (so new scripts never need registering)
    import subprocess, re
    try:
        out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []
    jobs = []
    for line in out.splitlines():
        mt = re.search(r"research/(\w+)\.py", line)
        if mt and "dashboard" not in line:
            name = mt.group(1)
            jobs.append(RESEARCH_JOBS.get(name, name.replace("_", " ")))
    return sorted(set(jobs))


def build():
    if not LEDGER.exists():
        return "<h2>No strategy ledger yet.</h2>"
    d = json.loads(LEDGER.read_text())
    now = d.get("now", {})
    jobs = running_now()
    if jobs:
        now_html = f'<div class=now><span class=live>● RUNNING</span> &nbsp;{" · ".join(jobs)}</div>'
    else:
        nxt = now.get("next", [])
        now_html = (f'<div class=now><span class=idle>○ idle</span> &nbsp;focus: <b>{now.get("focus","—")}</b>'
                    + (f' &nbsp;·&nbsp; next: {" → ".join(nxt[:3])}' if nxt else '') + '</div>')
    strat = d.get("strategies", [])
    counts = {}
    for s in strat: counts[s["status"]] = counts.get(s["status"], 0) + 1
    chips = " ".join(f'<span style="color:{SC.get(k,"#888")}">●</span> {v} {k}' for k, v in counts.items())

    cards = []
    order = {"validated": 0, "candidate": 1, "testing": 2, "queued": 3, "rejected": 4}
    for s in sorted(strat, key=lambda x: order.get(x["status"], 9)):
        mt = s.get("metrics", {})
        c = SC.get(s["status"], "#888")
        cards.append(f"""
        <div class=card style="border-left:4px solid {c}">
          <div class=row><span class=name>{s['name']}</span>
            <span class=badge style="background:{c}22;color:{c}">{s['status'].upper()}</span></div>
          <div class=tags>{s['book']} · {s['side']} · {s['horizon']}</div>
          <div class=hyp>{s['hypothesis']}</div>
          <div class=metrics>
            IC <b>{m(mt.get('ic'))}</b> · t <b>{m(mt.get('t'))}</b> · Sharpe <b>{m(mt.get('sharpe'))}</b>
            · maxDD <b>{m(mt.get('maxdd'),pct=True)}</b> · OOS <b>{m(mt.get('oos'))}</b>
            · cost-adj <b>{m(mt.get('cost_adj'))}</b> · trials {mt.get('n_trials',0)} · obs {mt.get('n_obs',0)}
          </div>
          <div class=ev>📚 {s.get('evidence_prior','')}</div>
          <div class=notes>▸ {s.get('notes','')}  <span class=upd>({s.get('updated','')})</span></div>
        </div>""")

    learns = "".join(f'<tr><td class=ld>{l["date"]}</td><td>{l["learning"]}'
                     f'<div class=imp>→ {l.get("impact","")}</div></td></tr>'
                     for l in d.get("learnings", []))
    disc = d.get("discovered", {})
    disc_html = ""
    if disc:
        drows = "".join(
            f'<tr><td>{s["feature"]}</td><td>{s["horizon"]}d</td>'
            f'<td>{s["ic_oos"]:+.3f}</td><td>{s["t_oos"]:+.2f}</td><td>{s.get("ic_is",0):+.3f}</td></tr>'
            for s in disc.get("survivors", []))
        disc_html = f"""
        <h3>🔎 Open-ended discovery <span class=sub>({disc.get('n_tested','?')} feature/combo tests · {disc.get('method','')})</span></h3>
        <div class=warn>⚠ {disc.get('caveat','')}</div>
        <table><tr><th>signal / combo</th><th>horizon</th><th>IC&nbsp;oos</th><th>t&nbsp;oos</th><th>IC&nbsp;is</th></tr>{drows}</table>"""
    return f"""
    <h1>🧠 Strategy Board</h1>
    <p class=sub>{len(strat)} candidate strategies · {chips} · ledger updated {dt.datetime.fromtimestamp(os.path.getmtime(LEDGER)).strftime('%Y-%m-%d %H:%M')} ·
      page {dt.datetime.now().strftime('%H:%M:%S')}</p>
    {now_html}
    <p class=disc>Nothing is <b style="color:#22c55e">validated</b> until it clears t&gt;3 in BOTH halves + cost haircut.
      Metrics fill in as backfills mature and Tier-0 runs.</p>
    {''.join(cards)}
    {disc_html}
    <h3>Learnings ledger</h3>
    <table>{learns}</table>
    """


HTML = """<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=120>
<title>Strategy Board</title><style>
body{{background:#0b1120;color:#e2e8f0;font:14px -apple-system,Segoe UI,Arial;margin:24px;max-width:1000px}}
h1{{margin:0}} .sub{{color:#94a3b8;font-size:12px;margin:4px 0}} .disc{{color:#64748b;font-size:12px;margin:0 0 16px}}
h3{{margin:24px 0 8px}} .card{{background:#0f172a;border-radius:8px;padding:12px 14px;margin:10px 0}}
.row{{display:flex;justify-content:space-between;align-items:center}} .name{{font-size:16px;font-weight:600}}
.badge{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px}}
.tags{{color:#94a3b8;font-size:12px;margin:2px 0 6px}} .hyp{{font-size:13px;margin:4px 0}}
.metrics{{font-size:12px;color:#cbd5e1;background:#0b1120;padding:6px 8px;border-radius:5px;margin:6px 0}}
.metrics b{{color:#f1f5f9}} .ev{{color:#7c8aa0;font-size:11.5px;margin:4px 0}}
.notes{{color:#94a3b8;font-size:12px}} .upd{{color:#475569}}
table{{border-collapse:collapse;width:100%}} td{{padding:6px 8px;border-bottom:1px solid #1e293b;vertical-align:top}}
.ld{{color:#64748b;white-space:nowrap;font-size:12px}} .warn{{background:#422006;color:#fbbf24;padding:8px 10px;border-radius:6px;font-size:12px;margin:6px 0}} .imp{{color:#22c55e;font-size:12px;margin-top:2px}} .now{{background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:8px 12px;margin:0 0 16px;font-size:13px}} .live{{color:#22c55e;font-weight:700}} .idle{{color:#64748b;font-weight:700}}
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
    print(f"strategy board -> http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
