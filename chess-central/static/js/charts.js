// Hand-rolled SVG charts following the house dataviz rules:
// thin marks, rounded data-ends, recessive grid, hover tooltips, legends for 2+.
const NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)",
                "var(--series-4)", "var(--series-5)"];

function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span / count)));
  const err = (span / count) / step;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  const s = step * mult;
  const lo = Math.floor(min / s) * s, hi = Math.ceil(max / s) * s;
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += s) ticks.push(Math.round(v * 100) / 100);
  return ticks;
}

function tooltip(wrap) {
  let tip = wrap.querySelector(".chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "chart-tip";
    wrap.append(tip);
  }
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.style.display = "block";
      const r = wrap.getBoundingClientRect();
      let left = x + 12, top = y - 10;
      if (left + tip.offsetWidth > r.width - 4) left = x - tip.offsetWidth - 12;
      tip.style.left = `${Math.max(2, left)}px`;
      tip.style.top = `${Math.max(2, top)}px`;
    },
    hide() { tip.style.display = "none"; },
  };
}

// series: [{name, points:[{x: Date|number, y: number}]}] — multi-series line chart
export function lineChart(container, series, { height = 220, yLabel = "" } = {}) {
  container.innerHTML = "";
  container.classList.add("chart-wrap");
  const W = 720, H = height, M = { t: 14, r: 14, b: 26, l: 44 };
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });

  const all = series.flatMap(s => s.points);
  if (!all.length) { container.innerHTML = '<div class="empty">No data yet</div>'; return; }
  const xs = all.map(p => +p.x), ys = all.map(p => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const yTicks = niceTicks(Math.min(...ys), Math.max(...ys));
  const y0 = yTicks[0], y1 = yTicks[yTicks.length - 1];
  const X = v => M.l + (W - M.l - M.r) * (x1 === x0 ? 0.5 : (v - x0) / (x1 - x0));
  const Y = v => H - M.b - (H - M.t - M.b) * ((v - y0) / (y1 - y0 || 1));

  for (const t of yTicks) {
    svg.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: Y(t), y2: Y(t),
      stroke: "var(--grid)", "stroke-width": 1 }));
    const lbl = svgEl("text", { x: M.l - 8, y: Y(t) + 4, "text-anchor": "end",
      "font-size": 11, fill: "var(--muted)" });
    lbl.textContent = t;
    svg.append(lbl);
  }
  const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", year: "2-digit" });
  const xTickCount = Math.min(5, new Set(xs).size);
  for (let i = 0; i < xTickCount; i++) {
    const v = x0 + (x1 - x0) * (xTickCount === 1 ? 0.5 : i / (xTickCount - 1));
    const lbl = svgEl("text", { x: X(v), y: H - 8, "text-anchor": "middle",
      "font-size": 11, fill: "var(--muted)" });
    lbl.textContent = dateFmt.format(new Date(v));
    svg.append(lbl);
  }

  series.forEach((s, i) => {
    const pts = [...s.points].sort((a, b) => +a.x - +b.x);
    const d = pts.map((p, j) => `${j ? "L" : "M"}${X(+p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
    svg.append(svgEl("path", { d, fill: "none", stroke: SERIES[i % SERIES.length],
      "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = pts[pts.length - 1];
    svg.append(svgEl("circle", { cx: X(+last.x), cy: Y(last.y), r: 4,
      fill: SERIES[i % SERIES.length], stroke: "var(--surface)", "stroke-width": 2 }));
  });

  const tip = tooltip(container);
  const hover = svgEl("line", { y1: M.t, y2: H - M.b, stroke: "var(--baseline)",
    "stroke-width": 1, "stroke-dasharray": "3,3", visibility: "hidden" });
  svg.append(hover);
  svg.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) * (W / r.width);
    const xv = x0 + (x1 - x0) * ((mx - M.l) / (W - M.l - M.r));
    hover.setAttribute("x1", mx); hover.setAttribute("x2", mx);
    hover.setAttribute("visibility", "visible");
    const rows = series.map((s, i) => {
      let best = null;
      for (const p of s.points) if (!best || Math.abs(+p.x - xv) < Math.abs(+best.x - xv)) best = p;
      return best ? `<span class="sw" style="background:${SERIES[i % SERIES.length]}"></span>${s.name}: <b>${best.y}</b>` : "";
    }).filter(Boolean).join("<br>");
    tip.show(rows, ev.clientX - r.left, ev.clientY - r.top);
  });
  svg.addEventListener("mouseleave", () => { tip.hide(); hover.setAttribute("visibility", "hidden"); });

  container.append(svg);
  if (series.length > 1) {
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = series.map((s, i) =>
      `<span><span class="sw" style="background:${SERIES[i % SERIES.length]}"></span>${s.name}</span>`).join("");
    container.append(legend);
  }
}

// data: [{label, value, color?, sub?}] — horizontal bars, one measure
export function barChart(container, data, { height = null, unit = "", max = null } = {}) {
  container.innerHTML = "";
  container.classList.add("chart-wrap");
  if (!data.length) { container.innerHTML = '<div class="empty">No data yet</div>'; return; }
  const W = 720, rowH = 30, M = { t: 6, r: 46, b: 6, l: 150 };
  const H = height || data.length * rowH + M.t + M.b;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  const vmax = max ?? (Math.max(...data.map(d => d.value)) || 1);
  const tip = tooltip(container);

  data.forEach((d, i) => {
    const y = M.t + i * rowH;
    const w = Math.max(2, (W - M.l - M.r) * (d.value / vmax));
    const lbl = svgEl("text", { x: M.l - 10, y: y + rowH / 2 + 4, "text-anchor": "end",
      "font-size": 12, fill: "var(--ink-2)" });
    lbl.textContent = d.label.length > 22 ? d.label.slice(0, 21) + "…" : d.label;
    svg.append(lbl);
    const bar = svgEl("rect", { x: M.l, y: y + 7, width: w, height: rowH - 14,
      rx: 4, fill: d.color || "var(--series-1)" });
    bar.addEventListener("mousemove", ev => {
      const r = svg.getBoundingClientRect();
      tip.show(`${d.label}: <b>${d.value}${unit}</b>${d.sub ? `<br><span style="color:var(--muted)">${d.sub}</span>` : ""}`,
        (ev.clientX - r.left), (ev.clientY - r.top));
    });
    bar.addEventListener("mouseleave", () => tip.hide());
    svg.append(bar);
    const val = svgEl("text", { x: M.l + w + 8, y: y + rowH / 2 + 4,
      "font-size": 12, fill: "var(--ink)", "font-weight": 600 });
    val.textContent = `${d.value}${unit}`;
    svg.append(val);
  });
  svg.append(svgEl("line", { x1: M.l, x2: M.l, y1: M.t, y2: H - M.b,
    stroke: "var(--baseline)", "stroke-width": 1 }));
  container.append(svg);
}
