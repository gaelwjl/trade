/* Shared helpers for tradelab pages (inlined at build time by pages.render). */
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
