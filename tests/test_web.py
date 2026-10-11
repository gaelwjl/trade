import json

from tradelab.web import render, screener_page
from tradelab.web.signals_page import _day
from tradelab.live.signals import decisions
from tradelab.strategies.registry import REGISTRY
from tests.helpers import make_panel


def test_screener_payload_is_strict_json():
    p = make_panel(n_days=600, n_names=4)
    data = screener_page.build_payload(p, years=1)
    json.dumps(data, allow_nan=False)  # raises on NaN / numpy types
    assert len(data["dates"]) == 252 and set(data["symbols"]) == {
        r["symbol"] for r in data["rows"]
    }
    assert all(len(v) == 252 for v in data["symbols"]["S1"].values())


def test_signal_day_payload_is_strict_json():
    p = make_panel(n_days=400, n_names=30)
    for d in decisions(REGISTRY["reversal_1w"], p, 20):
        json.dumps(_day(d), allow_nan=False)


def test_render_inlines_payload_and_shared_assets():
    html = render.render("signals.html", {"x": 1})
    assert "/*__" not in html and '"x":1' in html and "--surface" in html


def test_nav_only_on_app_pages_and_plotly_external_when_served():
    static = render.render("backtests.html", {"x": 1})
    served = render.render(
        "backtests.html", {"x": 1}, nav="backtests", plotly_src="/static/plotly.js"
    )
    assert "<nav" not in static and len(static) > 1_000_000  # Plotly inlined offline
    assert 'aria-current="page"' in served and 'src="/static/plotly.js"' in served
    assert len(served) < 200_000


def test_templates_do_not_redeclare_shared_js_names():
    """A top-level const/let/function that common.js also declares is a SyntaxError in the
    browser ("Identifier has already been declared") and stops the whole page."""
    import re

    top = re.compile(
        r"^(?:const|let|function|async function)\s+([A-Za-z_$][\w$]*)", re.M
    )
    shared = set(top.findall((render.TEMPLATES / "common.js").read_text()))
    for page in render.TEMPLATES.glob("*.html"):
        clash = shared & set(top.findall(page.read_text()))
        assert not clash, f"{page.name} redeclares {sorted(clash)} from common.js"
