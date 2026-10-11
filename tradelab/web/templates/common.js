/* Shared helpers for tradelab pages (inlined into every page by render.render). */
const $ = (s) => document.querySelector(s);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ESC[ch]);  // data text -> safe HTML
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* persisted per-viewer state (all storage is best-effort) */
const store = {
  get(k, d) { try { const v = localStorage.getItem("tradelab." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("tradelab." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};

/* formatting */
const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const f2 = nf(2), f1 = nf(1), f0 = nf(0);
const MINUS = "−";
const sgn = (v, s) => (/[1-9]/.test(s) ? (v < 0 ? MINUS : "+") : "") + s;  // no sign on a value that rounds to zero
const pct = (v) => v == null ? "–" : sgn(v, f1.format(Math.abs(v) * 100) + "%");
const pctPlain = (v) => v == null ? "–" : (v < 0 ? MINUS : "") + f1.format(Math.abs(v) * 100) + "%";
const money = (v) => v == null ? "–" : "$" + f2.format(v);
const num1 = (v) => v == null ? "–" : f1.format(v);
const num2 = (v) => v == null ? "–" : (v < 0 ? MINUS : "") + f2.format(Math.abs(v));
const mult = (v) => v == null ? "–" : f1.format(v) + "×";

/* diverging cell tint: blue up / red down, intensity |v| / scale; text stays in ink */
function hexRgb(h) { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function heatBg(v, scale) {
  if (v == null) return "";
  const [r, g, b] = hexRgb(css(v >= 0 ? "--pos" : "--neg"));
  const a = Math.min(Math.abs(v) / scale, 1) * 0.38;
  return `background:rgba(${r},${g},${b},${a.toFixed(3)})`;
}

/* theme: stored choice wins, otherwise follow the OS; onChange re-renders theme-dependent output */
function initTheme(onChange) {
  const t = store.get("theme", null);
  if (t) document.documentElement.dataset.theme = t;
  $("#theme").addEventListener("click", () => {
    const next = getComputedStyle(document.documentElement).colorScheme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    onChange();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", onChange);
}

/* Plotly layout pieces that follow the current theme */
function plotTheme() {
  const surface = css("--surface"), line = css("--line");
  return {
    paper_bgcolor: surface, plot_bgcolor: surface,
    font: { color: css("--ink2"), family: "system-ui, sans-serif", size: 12 },
    hoverlabel: { bgcolor: surface, bordercolor: line, font: { color: css("--ink") } },
  };
}
const PLOT_CONFIG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

/* ---------- app pages: API calls and small UI helpers ---------- */
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ") : `HTTP ${res.status}`);
  }
  return data;
}
function toast(msg, ms = 3500) {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
  el.textContent = msg; el.style.display = "block";
  clearTimeout(el._t); el._t = setTimeout(() => { el.style.display = "none"; }, ms);
}
const usd = (v) => v == null ? "–" : (v < 0 ? MINUS : "") + "$" + f2.format(Math.abs(v));
const signedUsd = (v) => v == null ? "–" : sgn(v, "$" + f2.format(Math.abs(v)));
const qtyFmt = (v) => v == null ? "–" : (Number.isInteger(v) ? f0.format(v) : f2.format(v));
const UPS = new Set(["BUY", "ADD", "COVER"]), DOWNS = new Set(["SELL", "TRIM", "SHORT"]);
const actBadge = (a) => `<span class="act ${UPS.has(a) ? "up" : DOWNS.has(a) ? "down" : ""}">${esc(a)}</span>`;
const todayIso = () => new Date().toISOString().slice(0, 10);
const kpi = (k, v, sub = "") => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
function formJson(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "number") { if (el.value !== "") out[el.name] = Number(el.value); }
    else if (el.value !== "") out[el.name] = el.value;
  }
  return out;
}
function lineTrace(name, x, y, color, extra = {}) {
  return { type: "scatter", mode: "lines", name, x, y, line: { color, width: 2 }, ...extra };
}
function chartLayout(yfmt) {
  const grid = css("--grid"), line = css("--line"), ink2 = css("--ink2");
  return { ...plotTheme(), margin: { l: 56, r: 12, t: 8, b: 32 }, hovermode: "x unified",
    legend: { orientation: "h", x: 0, y: 1.12, font: { color: ink2 } },
    xaxis: { type: "date", showgrid: false, linecolor: line, tickfont: { color: ink2 } },
    yaxis: { gridcolor: grid, zeroline: true, zerolinecolor: line, tickformat: yfmt, tickfont: { color: ink2 } } };
}
