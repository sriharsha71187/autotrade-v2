"""The coach packet — a one-shot printable page for a human coach.

Everything a real coach would want before a first (or next) lesson:
who the player is, current ratings, recent form, the engine's read on his
strengths/weaknesses, his actual opening repertoire and where it leaks,
regular rivals, and the parent's journal notes. Served at /packet;
print it (Cmd+P) or save as PDF and email it.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from .. import config, db
from . import insights, repertoire

RATING_LABELS = {
    "nwsrs": "NWSRS", "uscf_regular": "USCF", "uscf_quick": "USCF Quick",
    "lichess_rapid": "Lichess Rapid", "lichess_blitz": "Lichess Blitz",
    "chesscom_rapid": "Chess.com Rapid", "chesscom_blitz": "Chess.com Blitz",
}


def _e(v) -> str:
    return html.escape(str(v if v is not None else "—"))


def render() -> str:
    p: list[str] = []
    name = config.get("player_name")
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    ratings = db.rows(
        """SELECT source, rating, date FROM ratings_history r1
           WHERE date = (SELECT MAX(date) FROM ratings_history r2
                         WHERE r2.source = r1.source)""")
    rating_cells = "".join(
        f"<div class='tile'><div class='lbl'>{_e(RATING_LABELS.get(r['source'], r['source']))}</div>"
        f"<div class='val'>{_e(r['rating'])}</div><div class='sub'>as of {_e(r['date'])}</div></div>"
        for r in ratings if r["source"] in RATING_LABELS)

    rec90 = db.row(
        """SELECT COUNT(*) n, SUM(result='win') w, SUM(result='loss') l,
                  SUM(result='draw') d FROM games
           WHERE played_at >= date('now', '-90 days')""") or {}
    acc90 = db.row(
        """SELECT AVG(MAX(m.winprob_before - m.winprob_after, 0)) loss,
                  SUM(m.classification='blunder') blunders, COUNT(*) moves
           FROM moves m JOIN games g ON g.id = m.game_id
           WHERE m.mover='player' AND g.played_at >= date('now', '-90 days')""") or {}

    p.append(f"""
<header>
  <h1>♞ {_e(name)}</h1>
  <div class="mut">Coach packet · generated {_e(today)} ·
    USCF {_e(config.get('uscf_id'))} · NWSRS {_e(config.get('nwsrs_id'))} ·
    {_e(config.get('home_area'))}</div>
</header>
<section>
  <h2>Ratings</h2>
  <div class="tiles">{rating_cells or "<div class='mut'>No ratings synced yet.</div>"}</div>
</section>
<section>
  <h2>Last 90 days</h2>
  <p>{_e(rec90.get('n') or 0)} games — {_e(rec90.get('w') or 0)}W
     {_e(rec90.get('l') or 0)}L {_e(rec90.get('d') or 0)}D.
     {f"Average win-chance lost per move: {acc90['loss']:.1f}%. "
      f"{acc90['blunders'] or 0} blunders over {acc90['moves']} analyzed moves."
      if acc90.get('loss') is not None else "No engine-analyzed moves in this window yet."}</p>
</section>""")

    # insights, grouped
    ins = insights.current()
    if ins:
        m = insights.meta()
        scope = (f"last {m['window_used']} days" if m.get("window_used")
                 else "all games") if m else "all games"
        p.append(f"<section><h2>Engine read ({_e(scope)})</h2>")
        for kind, title in (("weakness", "Weaknesses"),
                            ("opportunity", "Opportunities"),
                            ("strength", "Strengths")):
            items = [i for i in ins if i["kind"] == kind]
            if not items:
                continue
            p.append(f"<h3>{title}</h3><ul>")
            p.extend(f"<li><b>{_e(i['title'])}.</b> {_e(i['detail'])}</li>"
                     for i in items[:5])
            p.append("</ul>")
        p.append("</section>")

    # repertoire + left book
    p.append("<section><h2>Opening repertoire (from his own games)</h2>")
    for color in ("white", "black"):
        rep = repertoire.build(color)
        p.append(f"<h3>As {color}</h3>")
        if rep["findings"]:
            p.append("<ul>")
            p.extend(f"<li>{_e(f['text'])}</li>" for f in rep["findings"])
            p.append("</ul>")
        if rep["lines"]:
            p.append("<table><tr><th>Line</th><th>Games</th><th>Score</th></tr>")
            p.extend(
                f"<tr><td class='mono'>{_e(l['line'])}</td><td>{l['n']}</td>"
                f"<td>{l['score_pct']}% ({l['w']}W {l['l']}L {l['d']}D)</td></tr>"
                for l in rep["lines"][:6])
            p.append("</table>")
        elif not rep["findings"]:
            p.append("<p class='mut'>Not enough games yet.</p>")
    p.append("</section>")

    # recent games
    games = db.rows(
        """SELECT played_at, platform, color, opponent_name, opponent_rating,
                  result, opening_name, eco,
                  (SELECT COUNT(*) FROM moves m WHERE m.game_id=g.id
                     AND m.mover='player'
                     AND m.classification IN ('mistake','blunder')) AS mistakes
           FROM games g ORDER BY played_at DESC LIMIT 12""")
    if games:
        p.append("<section><h2>Recent games</h2>"
                 "<table><tr><th>Date</th><th>Opponent</th><th></th>"
                 "<th>Result</th><th>Opening</th><th>Mistakes</th></tr>")
        p.extend(
            f"<tr><td>{_e(g['played_at'][:10])}</td>"
            f"<td>{_e(g['opponent_name'])} ({_e(g['opponent_rating'])})</td>"
            f"<td>{'⚪' if g['color'] == 'white' else '⚫'}</td>"
            f"<td class='r-{_e(g['result'])}'>{_e(g['result'])}</td>"
            f"<td>{_e(g['opening_name'] or g['eco'])}</td>"
            f"<td>{_e(g['mistakes'])}</td></tr>"
            for g in games)
        p.append("</table></section>")

    # rivals
    rivals = db.rows("SELECT name, scout_report FROM rivals WHERE scout_report IS NOT NULL")
    if rivals:
        p.append("<section><h2>Regular rivals</h2>")
        for r in rivals:
            rep = json.loads(r["scout_report"])
            tops = [f["title"] for f in (rep.get("findings") or [])[:3]]
            p.append(f"<p><b>{_e(r['name'])}</b>"
                     + (": " + _e("; ".join(tops)) if tops else "") + "</p>")
        p.append("</section>")

    # latest AI weekly report
    note = db.row(
        """SELECT content, created_at FROM llm_notes WHERE kind='weekly_report'
           ORDER BY created_at DESC LIMIT 1""")
    if note:
        p.append(f"<section><h2>Latest coach's report ({_e(note['created_at'][:10])})</h2>"
                 f"<div class='pre'>{_e(note['content'])}</div></section>")

    # journal
    notes = db.rows("SELECT created_at, text FROM journal ORDER BY created_at DESC LIMIT 5")
    if notes:
        p.append("<section><h2>Parent / coach journal</h2>")
        p.extend(f"<p><span class='mut'>{_e(n['created_at'][:10])}</span> — "
                 f"{_e(n['text'])}</p>" for n in notes)
        p.append("</section>")

    body = "".join(p)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Coach packet — {_e(name)}</title>
<style>
  body {{ font: 14px/1.55 -apple-system, Georgia, serif; color: #1a1a1a;
          max-width: 820px; margin: 30px auto; padding: 0 24px; }}
  h1 {{ font-size: 26px; margin: 0; }}
  h2 {{ font-size: 17px; border-bottom: 2px solid #1a1a1a; padding-bottom: 4px;
        margin: 26px 0 10px; }}
  h3 {{ font-size: 14px; margin: 14px 0 6px; text-transform: uppercase;
        letter-spacing: .04em; }}
  .mut {{ color: #666; }} .mono {{ font-family: ui-monospace, monospace; font-size: 12.5px; }}
  .tiles {{ display: flex; gap: 14px; flex-wrap: wrap; }}
  .tile {{ border: 1px solid #ccc; border-radius: 8px; padding: 8px 14px; }}
  .tile .lbl {{ font-size: 11px; color: #666; text-transform: uppercase; }}
  .tile .val {{ font-size: 22px; font-weight: 700; }}
  .tile .sub {{ font-size: 11px; color: #666; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #ddd;
            font-size: 13px; }}
  th {{ font-size: 11px; text-transform: uppercase; color: #666; }}
  .r-win {{ color: #1a7f37; font-weight: 600; }} .r-loss {{ color: #b30000; }}
  .pre {{ white-space: pre-wrap; }}
  ul {{ margin: 6px 0; padding-left: 22px; }} li {{ margin: 4px 0; }}
  .noprint {{ background: #fff8e0; border: 1px solid #e0d090; border-radius: 8px;
              padding: 10px 14px; margin-bottom: 18px; }}
  @media print {{ .noprint {{ display: none; }} body {{ margin: 0; }}
                  section {{ break-inside: avoid; }} }}
</style></head><body>
<div class="noprint">🖨 Print this page (Cmd+P) or save it as a PDF to share
with a coach. It is generated fresh from the database each time.</div>
{body}
</body></html>"""
