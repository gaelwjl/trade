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


def render(template: str, payload: dict) -> str:
    from plotly.offline import get_plotlyjs

    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = (TEMPLATES / template).read_text()
    html = html.replace("/*__CSS__*/", (TEMPLATES / "common.css").read_text())
    html = html.replace("/*__COMMON_JS__*/", (TEMPLATES / "common.js").read_text())
    if "/*__PLOTLY__*/" in html:
        html = html.replace("/*__PLOTLY__*/", get_plotlyjs())
    return html.replace("null /*__DATA__*/", data)


def write(template: str, payload: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(template, payload))
    return out
