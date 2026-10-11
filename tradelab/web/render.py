"""Turn a template + JSON payload into one self-contained offline HTML file (shared CSS/JS, inlined Plotly)."""

from __future__ import annotations

import json
import math
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"


def plain(v: object) -> object:
    """numpy scalars -> Python, NaN -> None, so the payload is strict JSON."""
    v = getattr(v, "item", lambda: v)()
    return None if isinstance(v, float) and math.isnan(v) else v


def num(v: float | None, decimals: int) -> float | None:
    """Rounded float, NaN/None -> None."""
    return None if v is None or math.isnan(v) else round(float(v), decimals)


def series(values: list[float], decimals: int) -> list[float | None]:
    return [num(v, decimals) for v in values]


NAV = [
    ("today", "/", "Today"),
    ("portfolio", "/portfolio", "Portfolio"),
    ("strategy", "/strategy", "Strategy"),
    ("backtests", "/backtests", "Backtests"),
    ("signals", "/signals", "Signals"),
    ("screener", "/screener", "Screener"),
    ("settings", "/settings", "Settings and jobs"),
]


def nav_html(active: str | None) -> str:
    """Top navigation for pages served by the app; static exports pass ``None`` (no nav)."""
    if active is None:
        return ""
    current = ' aria-current="page"'
    links = "".join(
        f'<a href="{href}"{current if key == active else ""}>{label}</a>'
        for key, href, label in NAV
    )
    return f'<nav class="topnav" aria-label="Sections"><span class="brand">tradelab</span>{links}</nav>'


PLOTLY_SCRIPT = "<script>/*__PLOTLY__*/</script>"


def plotly_js() -> str:
    from plotly.offline import get_plotlyjs

    return get_plotlyjs()


def render(
    template: str,
    payload: dict | None,
    nav: str | None = None,
    plotly_src: str | None = None,
) -> str:
    """Fill a template. ``nav`` = active section (app pages); ``plotly_src`` = load Plotly
    from that URL instead of inlining it (pages served by the app)."""

    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace(
        "</", "<\\/"
    )
    html = (TEMPLATES / template).read_text()
    html = html.replace("/*__CSS__*/", (TEMPLATES / "common.css").read_text())
    html = html.replace("/*__COMMON_JS__*/", (TEMPLATES / "common.js").read_text())
    html = html.replace("<!--__NAV__-->", nav_html(nav))
    if plotly_src and PLOTLY_SCRIPT in html:
        html = html.replace(PLOTLY_SCRIPT, f'<script src="{plotly_src}"></script>')
    elif "/*__PLOTLY__*/" in html:
        html = html.replace("/*__PLOTLY__*/", plotly_js())
    return html.replace("null /*__DATA__*/", data)


def write(template: str, payload: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(template, payload))
    return out
