#!/usr/bin/env python3
"""Strategies board — the living ledger of candidate algorithms the research engine is
pursuing, their status + metrics, and the accumulating learnings. Reads
~/autotrade_strategies.json fresh on each load (I append to it as tests run).
Serves http://localhost:8790  (capture dash = 8788). Run: python research/strategy_dashboard.py
"""
import json, datetime as dt
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

LEDGER = Path.home() / "autotrade_strategies.json"
PORT = 8790
SC = {"validated": "#22c55e", "candidate": "#3b82f6", "testing": "#eab308",
      "queued": "#64748b", "rejected": "#ef4444"}


def m(v, pct=False, dp=2):
    if v is None: return '<span style="color:#475569">–</span>'
    return f"{v*100:.0f}%" if pct else f"{v:.{dp}f}"


def build():
    if not LEDGER.exists():
        return "<h2>No strategy ledger yet.</h2>"
    d = json.loads(LEDGER.read_text())
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
    return f"""
    <h1>🧠 Strategy Board</h1>
    <p class=sub>{len(strat)} candidate strategies · {chips} · ledger updated {d.get('updated','')} ·
      page {dt.datetime.now().strftime('%H:%M:%S')}</p>
    <p class=disc>Nothing is <b style="color:#22c55e">validated</b> until it clears t&gt;3 + OOS + cost haircut.
      Metrics fill in as backfills mature and Tier-0 runs.</p>
    {''.join(cards)}
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
.ld{{color:#64748b;white-space:nowrap;font-size:12px}} .imp{{color:#22c55e;font-size:12px;margin-top:2px}}
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
