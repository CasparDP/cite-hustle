"""Self-contained HTML dashboard (no network, no external assets).

The page embeds the repository's dashboard data as JSON and renders SVG charts with
a small inline script, so the file can be opened anywhere, synced, or attached.
Palette: muted, one hue per field, validated (light and dark) with the dataviz
skill's checker: CVD and normal-vision separation, chroma, and contrast all pass.
"""

from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path

from cite_hustle.collectors.journals import JournalRegistry
from cite_hustle.database.repository import ArticleRepository

FIELDS = ["accounting", "finance", "economics", "management"]


def build_payload(repo: ArticleRepository, db_path: Path) -> dict:
    data = repo.get_dashboard_data()
    journals = {
        j.issn: {"name": j.name, "field": j.field, "publisher": j.publisher}
        for j in JournalRegistry.get_by_field("all")
    }
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "host": platform.node(),
        "db_path": str(db_path),
        "fields": FIELDS,
        "journals": journals,
        **data,
    }


def render(payload: dict) -> str:
    # "</" inside JSON would close the <script> element early
    blob = json.dumps(payload, default=str).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", blob)


def write_dashboard(repo: ArticleRepository, db_path: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(build_payload(repo, db_path)), encoding="utf-8")
    return out_path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cite-hustle dashboard</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f3f0; --surface-1: #fcfcfb; --border: #e2e0da; --grid: #ebe9e4;
  --text-primary: #1b1b1a; --text-secondary: #52514e; --text-muted: #7a7974;
  --accent: #356fb0;
  --f-accounting: #356fb0; --f-finance: #c86a35; --f-economics: #8a5aa8; --f-management: #1f8a6b;
  --c-abstract: #356fb0; --c-pdf: #c86a35; --c-verified: #8a5aa8;
  --suspect: #b9b6ad; --flag-bg: #fbeee6; --flag-text: #8a3b12;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface-0: #121211; --surface-1: #1a1a19; --border: #33332f; --grid: #2a2a27;
    --text-primary: #f4f3ef; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
    --accent: #4a8bd0;
    --f-accounting: #4a8bd0; --f-finance: #d27a45; --f-economics: #a07ac0; --f-management: #2f9f7e;
    --c-abstract: #4a8bd0; --c-pdf: #d27a45; --c-verified: #a07ac0;
    --suspect: #5d5c57; --flag-bg: #3a2418; --flag-text: #f0b48f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1240px; margin: 0 auto; padding: 24px 20px 60px; }
h1 { font-size: 22px; margin: 0 0 2px; }
h2 { font-size: 16px; margin: 0 0 4px; }
h3 { font-size: 14px; margin: 16px 0 6px; color: var(--text-secondary); }
.sub, .note { color: var(--text-secondary); font-size: 12.5px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin-top: 16px; }
.filters { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-top: 14px; }
.filters label { color: var(--text-secondary); font-size: 13px; display: flex; gap: 6px; align-items: center; }
select { font: inherit; padding: 3px 6px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--surface-1); color: var(--text-primary); }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-top: 14px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.tile .k { color: var(--text-secondary); font-size: 12px; }
.tile .v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .d { color: var(--text-muted); font-size: 12px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 6px 0 2px; font-size: 12.5px; color: var(--text-secondary); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
svg text { fill: var(--text-secondary); font-size: 11px; }
svg .axis-title { fill: var(--text-muted); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr.clickable { cursor: pointer; }
tbody tr.clickable:hover, tbody tr.sel { background: var(--surface-0); }
.pill { display: inline-block; padding: 0 6px; border-radius: 9px; font-size: 11.5px;
  border: 1px solid var(--border); color: var(--text-secondary); white-space: nowrap; }
.pill.flag { background: var(--flag-bg); color: var(--flag-text); border-color: transparent; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
details summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; margin-top: 8px; }
#tip { position: fixed; pointer-events: none; z-index: 10; display: none; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 8px; padding: 7px 9px; font-size: 12.5px;
  box-shadow: 0 4px 14px rgba(0,0,0,.12); min-width: 140px; }
#tip .row { display: flex; justify-content: space-between; gap: 14px; }
#tip .row b { font-variant-numeric: tabular-nums; }
.scroll { max-height: 420px; overflow: auto; }
</style>
</head>
<body>
<main>
  <h1>cite-hustle</h1>
  <div class="sub" id="meta"></div>

  <div class="filters">
    <label>Field <select id="f-field"></select></label>
    <label>From <select id="f-from"></select></label>
    <label>To <select id="f-to"></select></label>
    <label><input type="checkbox" id="f-suspect"> Exclude suspected non-articles</label>
  </div>

  <div class="tiles" id="tiles"></div>

  <div class="grid2">
    <div class="card">
      <h2>Articles per year</h2>
      <div class="note">Stacked by field. Hover a segment for counts.</div>
      <div class="legend" id="lg-years"></div>
      <div id="ch-years"></div>
    </div>
    <div class="card">
      <h2>Coverage per year</h2>
      <div class="note">Share of articles with an abstract, any PDF, and a verified PDF.</div>
      <div class="legend" id="lg-cov"></div>
      <div id="ch-cov"></div>
    </div>
  </div>

  <div class="card">
    <h2>Journals</h2>
    <div class="note">Click a journal for its detail view. Click a column header to sort.</div>
    <div class="scroll"><table id="t-journals"></table></div>
  </div>

  <div class="card" id="detail" style="display:none"></div>

  <div class="card">
    <h2>Unusual year-over-year changes</h2>
    <div class="note">Journal-years that differ by at least 40% (and 15 articles) from the previous
      year. Usually incomplete collection, moved publication dates, or editorial changes.</div>
    <div class="scroll"><table id="t-jumps"></table></div>
  </div>

  <div class="grid2">
    <div class="card">
      <h2>Processing, last 30 days</h2>
      <div class="scroll"><table id="t-activity"></table></div>
    </div>
    <div class="card">
      <h2>PDFs by source and verification</h2>
      <table id="t-pdfs"></table>
    </div>
  </div>

  <p class="note">Suspected non-articles: untitled issue-level DOIs, or a title that occurs at
    least three times in one journal (mastheads, "Forthcoming Papers", prize notices).
    "SSRN searched" counts articles with any recorded SSRN outcome.</p>
</main>
<div id="tip"></div>

<script id="payload" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("payload").textContent);
const FIELD_LABEL = {accounting: "Accounting", finance: "Finance", economics: "Economics", management: "Management"};
const fieldOf = issn => (D.journals[issn] || {}).field || "other";
const nameOf = (issn, fallback) => (D.journals[issn] || {}).name || fallback || issn;
const fmt = n => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const pct = (a, b) => (b ? (100 * a / b) : 0);
const fmtPct = (a, b) => (b ? pct(a, b).toFixed(0) + "%" : "–");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// ---- tooltip ---------------------------------------------------------------
const tip = document.getElementById("tip");
function showTip(ev, title, rows) {
  tip.innerHTML = `<div><b>${esc(title)}</b></div>` + rows.map(([k, v, color]) =>
    `<div class="row"><span>${color ? `<i class="sw" style="background:${color}"></i> ` : ""}${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
  tip.style.display = "block";
  const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = Math.min(ev.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.display = "none"; };

// ---- filters ---------------------------------------------------------------
const allYears = [...new Set(D.journal_years.map(r => r.year))].filter(y => y != null).sort((a, b) => a - b);
const state = {field: "all", from: Math.max(allYears[0], 2000), to: allYears[allYears.length - 1], noSuspect: false, journal: null};
function initFilters() {
  const f = document.getElementById("f-field");
  f.innerHTML = `<option value="all">All fields</option>` + D.fields.map(x => `<option value="${x}">${FIELD_LABEL[x]}</option>`).join("");
  for (const id of ["f-from", "f-to"]) {
    document.getElementById(id).innerHTML = allYears.map(y => `<option>${y}</option>`).join("");
  }
  document.getElementById("f-from").value = state.from;
  document.getElementById("f-to").value = state.to;
  f.onchange = e => { state.field = e.target.value; renderAll(); };
  document.getElementById("f-from").onchange = e => { state.from = +e.target.value; renderAll(); };
  document.getElementById("f-to").onchange = e => { state.to = +e.target.value; renderAll(); };
  document.getElementById("f-suspect").onchange = e => { state.noSuspect = e.target.checked; renderAll(); };
}

// Journal-year rows after filters, with suspected non-articles optionally removed
// (subtracting suspects assumes they have no abstract/PDF; close enough for coverage).
function rows(issn) {
  return D.journal_years.filter(r =>
    r.year >= state.from && r.year <= state.to &&
    (state.field === "all" || fieldOf(r.journal_issn) === state.field) &&
    (!issn || r.journal_issn === issn)
  ).map(r => state.noSuspect ? {...r, n: r.n - r.n_suspect, n_suspect: 0} : r);
}
const sum = (rs, k) => rs.reduce((s, r) => s + (r[k] || 0), 0);
const yearsInRange = () => allYears.filter(y => y >= state.from && y <= state.to);

// ---- charts ----------------------------------------------------------------
function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function frame(container, {height = 260, yMax, yTitle, yFmt = fmt, years}) {
  container.innerHTML = "";
  const W = container.clientWidth || 560, H = height, m = {l: 54, r: 10, t: 10, b: 26};
  const svg = el("svg", {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img"}, container);
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const y = v => m.t + ih - (v / yMax) * ih;
  for (let i = 0; i <= 4; i++) {
    const v = yMax * i / 4;
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), style: "stroke:var(--grid)"}, svg);
    el("text", {x: m.l - 6, y: y(v) + 4, "text-anchor": "end"}, svg).textContent = yFmt(v);
  }
  const t = el("text", {class: "axis-title", transform: `translate(14 ${m.t + ih / 2}) rotate(-90)`, "text-anchor": "middle"}, svg);
  t.textContent = yTitle;
  const step = iw / Math.max(years.length, 1);
  const every = Math.ceil(years.length / Math.max(1, Math.floor(iw / 42)));
  years.forEach((yr, i) => {
    if (i % every === 0 || i === years.length - 1)
      el("text", {x: m.l + step * (i + 0.5), y: H - 8, "text-anchor": "middle"}, svg).textContent = yr;
  });
  return {svg, m, iw, ih, y, step, W, H};
}

// Bar with rounded top corners only (data end), square at the baseline.
function topRounded(x, y0, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y0 + h}V${y0 + r}Q${x},${y0} ${x + r},${y0}H${x + w - r}Q${x + w},${y0} ${x + w},${y0 + r}V${y0 + h}Z`;
}

function stackedBars(container, years, series, yTitle) {
  const totals = years.map((_, i) => series.reduce((s, se) => s + se.values[i], 0));
  const f = frame(container, {yMax: niceMax(Math.max(...totals, 1)), yTitle, years});
  const bw = Math.max(2, Math.min(28, f.step * 0.72));
  years.forEach((yr, i) => {
    const x = f.m.l + f.step * i + (f.step - bw) / 2;
    let acc = 0;
    const visible = series.filter(se => se.values[i] > 0);
    visible.forEach((se, j) => {
      const v = se.values[i], y1 = f.y(acc + v), y0 = f.y(acc);
      const h = Math.max(0, y0 - y1 - (j > 0 ? 2 : 0));  // 2px surface gap between segments
      const isTop = j === visible.length - 1;
      const shape = isTop ? el("path", {d: topRounded(x, y1, bw, h, 4)}, f.svg)
                          : el("rect", {x, y: y1, width: bw, height: h}, f.svg);
      shape.setAttribute("style", `fill:${se.color}`);
      acc += v;
    });
    const hit = el("rect", {x: f.m.l + f.step * i, y: f.m.t, width: f.step, height: f.ih, fill: "transparent"}, f.svg);
    hit.addEventListener("mousemove", ev => showTip(ev, String(yr),
      [...series.filter(se => se.values[i] > 0).map(se => [se.label, fmt(se.values[i]), se.color]).reverse(),
       ["Total", fmt(totals[i])]]));
    hit.addEventListener("mouseleave", hideTip);
  });
}

function lines(container, years, series, yTitle) {
  const f = frame(container, {yMax: 100, yTitle, yFmt: v => v + "%", years});
  const xs = i => f.m.l + f.step * (i + 0.5);
  for (const se of series) {
    const pts = se.values.map((v, i) => v == null ? null : `${xs(i)},${f.y(v)}`).filter(Boolean);
    el("polyline", {points: pts.join(" "), fill: "none", style: `stroke:${se.color};stroke-width:2`}, f.svg);
  }
  const cross = el("line", {y1: f.m.t, y2: f.m.t + f.ih, style: "stroke:var(--text-muted);stroke-width:1;display:none"}, f.svg);
  const dots = series.map(se => el("circle", {r: 4, style: `fill:${se.color};stroke:var(--surface-1);stroke-width:2;display:none`}, f.svg));
  const overlay = el("rect", {x: f.m.l, y: f.m.t, width: f.iw, height: f.ih, fill: "transparent"}, f.svg);
  overlay.addEventListener("mousemove", ev => {
    const r = f.svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(years.length - 1, Math.floor((ev.clientX - r.left - f.m.l) / f.step)));
    cross.setAttribute("x1", xs(i)); cross.setAttribute("x2", xs(i)); cross.style.display = "";
    series.forEach((se, k) => {
      const v = se.values[i];
      dots[k].style.display = v == null ? "none" : "";
      if (v != null) { dots[k].setAttribute("cx", xs(i)); dots[k].setAttribute("cy", f.y(v)); }
    });
    showTip(ev, String(years[i]), series.map(se => [se.label, se.values[i] == null ? "–" : se.values[i].toFixed(0) + "%", se.color]));
  });
  overlay.addEventListener("mouseleave", () => { cross.style.display = "none"; dots.forEach(d => d.style.display = "none"); hideTip(); });
}

function legend(id, items) {
  document.getElementById(id).innerHTML = items.map(([label, color]) =>
    `<span><i class="sw" style="background:${color}"></i>${esc(label)}</span>`).join("");
}

function sparkline(values) {
  const w = 90, h = 22, max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / Math.max(values.length - 1, 1)) * (w - 2) + 1},${h - 2 - (v / max) * (h - 4)}`).join(" ");
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" style="stroke:var(--accent);stroke-width:1.5"/></svg>`;
}

const COV = [
  ["Abstract", "n_abstract", "var(--c-abstract)"],
  ["Any PDF", "n_pdf", "var(--c-pdf)"],
  ["Verified PDF", "n_verified", "var(--c-verified)"],
];
function coverageSeries(rs, years) {
  return COV.map(([label, key, color]) => ({label, color, values: years.map(y => {
    const yr = rs.filter(r => r.year === y), n = sum(yr, "n");
    return n ? pct(sum(yr, key), n) : null;
  })}));
}

// ---- sections --------------------------------------------------------------
function renderMeta() {
  document.getElementById("meta").textContent =
    `Generated ${D.generated} on ${D.host} · ${D.db_path}`;
}

function renderTiles() {
  const rs = rows(), n = sum(rs, "n");
  const journalsShown = new Set(rs.map(r => r.journal_issn)).size;
  const tiles = [
    ["Articles", fmt(n), `${journalsShown} journals, ${state.from}–${state.to}`],
    ["With abstract", fmtPct(sum(rs, "n_abstract"), n), `${fmt(n - sum(rs, "n_abstract"))} missing`],
    ["SSRN searched", fmtPct(sum(rs, "n_ssrn_searched"), n), `${fmt(n - sum(rs, "n_ssrn_searched"))} pending · ${fmt(sum(rs, "n_ssrn_found"))} found`],
    ["PDFs", fmt(sum(rs, "n_pdf")), `${fmtPct(sum(rs, "n_pdf"), n)} of articles`],
    ["Verified PDFs", fmt(sum(rs, "n_verified")), `${fmtPct(sum(rs, "n_verified"), sum(rs, "n_pdf"))} of PDFs`],
    ["In wiki", fmt(sum(rs, "n_wiki")), "ingested source pages"],
    ["Suspected non-articles", fmt(sum(D.journal_years.filter(r => r.year >= state.from && r.year <= state.to && (state.field === "all" || fieldOf(r.journal_issn) === state.field)), "n_suspect")),
      state.noSuspect ? "excluded from counts" : "included in counts"],
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([k, v, d]) =>
    `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");
}

function renderOverviewCharts() {
  const rs = rows(), years = yearsInRange();
  const fields = state.field === "all" ? D.fields : [state.field];
  const series = fields.map(fd => ({label: FIELD_LABEL[fd], color: `var(--f-${fd})`,
    values: years.map(y => sum(rs.filter(r => r.year === y && fieldOf(r.journal_issn) === fd), "n"))}));
  legend("lg-years", series.length > 1 ? series.map(s => [s.label, s.color]) : []);
  stackedBars(document.getElementById("ch-years"), years, series, "Articles");
  legend("lg-cov", COV.map(([l, , c]) => [l, c]));
  lines(document.getElementById("ch-cov"), years, coverageSeries(rs, years), "% of articles");
}

let sortKey = "n", sortDir = -1;
function renderJournalTable() {
  const rs = rows(), years = yearsInRange();
  const by = {};
  for (const r of rs) (by[r.journal_issn] ||= []).push(r);
  const list = Object.entries(by).map(([issn, jr]) => {
    const n = sum(jr, "n");
    return {issn, name: nameOf(issn, jr[0].journal_name), field: fieldOf(issn), n,
      latest: Math.max(...jr.filter(r => r.n > 0).map(r => r.year)),
      abs: pct(sum(jr, "n_abstract"), n), pdf: pct(sum(jr, "n_pdf"), n),
      ver: sum(jr, "n_verified"), sus: sum(D.journal_years.filter(r => r.journal_issn === issn && r.year >= state.from && r.year <= state.to), "n_suspect"),
      spark: years.map(y => sum(jr.filter(r => r.year === y), "n"))};
  });
  list.sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
  const cols = [["name", "Journal"], ["field", "Field"], ["n", "Articles", 1], ["latest", "Latest", 1],
    ["abs", "Abstract", 1], ["pdf", "PDF", 1], ["ver", "Verified", 1], ["sus", "Suspect", 1], [null, `Per year ${state.from}–${state.to}`]];
  const t = document.getElementById("t-journals");
  t.innerHTML = `<thead><tr>${cols.map(([k, l, num]) => `<th class="${num ? "num" : ""}" data-k="${k ?? ""}">${l}${k === sortKey ? (sortDir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>` +
    `<tbody>${list.map(j => `<tr class="clickable${j.issn === state.journal ? " sel" : ""}" data-issn="${j.issn}">
      <td>${esc(j.name)}</td><td>${FIELD_LABEL[j.field] || j.field}</td><td class="num">${fmt(j.n)}</td>
      <td class="num">${j.latest > 0 ? j.latest : "–"}</td><td class="num">${j.abs.toFixed(0)}%</td>
      <td class="num">${j.pdf.toFixed(0)}%</td><td class="num">${fmt(j.ver)}</td>
      <td class="num">${j.sus ? `<span class="pill flag">${fmt(j.sus)}</span>` : "0"}</td><td>${sparkline(j.spark)}</td></tr>`).join("")}</tbody>`;
  t.querySelectorAll("th").forEach(th => th.onclick = () => {
    const k = th.dataset.k; if (!k) return;
    sortDir = k === sortKey ? -sortDir : (k === "name" || k === "field" ? 1 : -1); sortKey = k; renderJournalTable();
  });
  t.querySelectorAll("tbody tr").forEach(tr => tr.onclick = () => {
    state.journal = tr.dataset.issn; renderJournalTable(); renderDetail();
    document.getElementById("detail").scrollIntoView({behavior: "smooth", block: "start"});
  });
}

function articleTable(list, withStatus) {
  if (!list.length) return `<p class="note">None.</p>`;
  return `<table><thead><tr><th class="num">Year</th><th>Title</th>${withStatus ? "<th>Status</th>" : ""}</tr></thead><tbody>` +
    list.map(a => `<tr><td class="num">${a.year ?? ""}</td><td><a href="https://doi.org/${encodeURI(a.doi)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <div class="note">${esc((a.authors || "").slice(0, 140))}</div></td>${withStatus ? `<td>${[
        a.has_abstract ? `<span class="pill">abstract</span>` : "",
        a.ssrn_found ? `<span class="pill">SSRN</span>` : "",
        a.pdf_source ? `<span class="pill">PDF ${esc(a.pdf_source)}${a.verify_status ? " · " + esc(a.verify_status) : ""}</span>` : "",
        a.wiki_status ? `<span class="pill">wiki ${esc(a.wiki_status)}</span>` : ""].join(" ")}</td>` : ""}</tr>`).join("") + "</tbody></table>";
}

function renderDetail() {
  const box = document.getElementById("detail");
  if (!state.journal) { box.style.display = "none"; return; }
  const issn = state.journal, j = D.journals[issn] || {};
  const years = yearsInRange();
  const all = D.journal_years.filter(r => r.journal_issn === issn && r.year >= state.from && r.year <= state.to);
  const rs = rows(issn);
  box.style.display = "";
  box.innerHTML = `<h2>${esc(nameOf(issn, all[0] && all[0].journal_name))}</h2>
    <div class="note">ISSN ${esc(issn)} · ${esc(FIELD_LABEL[j.field] || "")} · ${esc(j.publisher || "")}</div>
    <div class="grid2">
      <div><h3>Articles per year</h3><div class="legend" id="lg-d1"></div><div id="ch-d1"></div></div>
      <div><h3>Coverage per year</h3><div class="legend" id="lg-d2"></div><div id="ch-d2"></div></div>
    </div>
    <details><summary>Per-year table</summary><div class="scroll"><table>
      <thead><tr><th class="num">Year</th><th class="num">Articles</th><th class="num">Suspect</th><th class="num">Abstract</th>
      <th class="num">SSRN found</th><th class="num">PDF</th><th class="num">Verified</th><th class="num">Wiki</th></tr></thead><tbody>
      ${[...all].reverse().map(r => `<tr><td class="num">${r.year}</td><td class="num">${fmt(r.n)}</td><td class="num">${fmt(r.n_suspect)}</td>
        <td class="num">${fmt(r.n_abstract)}</td><td class="num">${fmt(r.n_ssrn_found)}</td><td class="num">${fmt(r.n_pdf)}</td>
        <td class="num">${fmt(r.n_verified)}</td><td class="num">${fmt(r.n_wiki)}</td></tr>`).join("")}
    </tbody></table></div></details>
    <h3>Most recent articles</h3><div class="scroll">${articleTable(D.recent_articles.filter(a => a.journal_issn === issn && !a.suspect), true)}</div>
    <h3>Suspected non-articles (most recent)</h3><div class="scroll">${articleTable(D.recent_articles.filter(a => a.journal_issn === issn && a.suspect), false)}</div>`;
  const research = years.map(y => { const r = all.find(x => x.year === y); return r ? r.n - r.n_suspect : 0; });
  const suspect = years.map(y => { const r = all.find(x => x.year === y); return r && !state.noSuspect ? r.n_suspect : 0; });
  const series = [{label: "Articles", color: `var(--f-${j.field || "accounting"})`, values: research}];
  if (suspect.some(v => v > 0)) series.push({label: "Suspected non-articles", color: "var(--suspect)", values: suspect});
  legend("lg-d1", series.length > 1 ? series.map(s => [s.label, s.color]) : []);
  stackedBars(document.getElementById("ch-d1"), years, series, "Articles");
  legend("lg-d2", COV.map(([l, , c]) => [l, c]));
  lines(document.getElementById("ch-d2"), years, coverageSeries(rs, years), "% of articles");
}

function renderJumps() {
  const out = [];
  const byJ = {};
  for (const r of D.journal_years) (byJ[r.journal_issn] ||= {})[r.year] = r.n - (state.noSuspect ? r.n_suspect : 0);
  for (const [issn, m] of Object.entries(byJ)) {
    if (state.field !== "all" && fieldOf(issn) !== state.field) continue;
    for (const y of Object.keys(m).map(Number)) {
      if (y < state.from || y > state.to) continue;
      const prev = m[y - 1] ?? 0, cur = m[y];
      if (!(y - 1 in m) && cur < 15) continue;
      const diff = cur - prev;
      if (Math.abs(diff) >= 15 && Math.abs(diff) >= 0.4 * Math.max(prev, 1)) out.push({issn, y, prev, cur, diff});
    }
  }
  out.sort((a, b) => b.y - a.y || Math.abs(b.diff) - Math.abs(a.diff));
  document.getElementById("t-jumps").innerHTML = out.length ?
    `<thead><tr><th>Journal</th><th class="num">Year</th><th class="num">Previous</th><th class="num">This year</th><th class="num">Change</th></tr></thead><tbody>` +
    out.map(o => `<tr class="clickable" data-issn="${o.issn}"><td>${esc(nameOf(o.issn))}</td><td class="num">${o.y}</td><td class="num">${fmt(o.prev)}</td>
      <td class="num">${fmt(o.cur)}</td><td class="num">${o.diff > 0 ? "+" : ""}${fmt(o.diff)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="note">No unusual changes in this range.</td></tr></tbody>`;
  document.querySelectorAll("#t-jumps tbody tr.clickable").forEach(tr => tr.onclick = () => {
    state.journal = tr.dataset.issn; renderJournalTable(); renderDetail();
    document.getElementById("detail").scrollIntoView({behavior: "smooth", block: "start"});
  });
}

function renderTables() {
  document.getElementById("t-activity").innerHTML = D.activity.length ?
    `<thead><tr><th>Day</th><th>Stage</th><th>Status</th><th class="num">Rows</th></tr></thead><tbody>` +
    D.activity.map(a => `<tr><td>${esc(a.day)}</td><td>${esc(a.stage)}</td><td>${esc(a.status)}</td><td class="num">${fmt(a.n)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="note">No processing in the last 30 days.</td></tr></tbody>`;
  document.getElementById("t-pdfs").innerHTML =
    `<thead><tr><th>Source</th><th>Verification</th><th class="num">PDFs</th></tr></thead><tbody>` +
    D.pdf_sources.map(p => `<tr><td>${esc(p.source)}</td><td>${esc(p.verify_status)}</td><td class="num">${fmt(p.n)}</td></tr>`).join("") + "</tbody>";
}

function renderAll() {
  renderTiles(); renderOverviewCharts(); renderJournalTable(); renderDetail(); renderJumps();
}
renderMeta(); initFilters(); renderTables(); renderAll();
let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(renderAll, 150); });
</script>
</body>
</html>
"""
