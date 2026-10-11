import json

from tradelab.dashboard import render, screener_page
from tradelab.dashboard.signals_page import _day
from tradelab.live.signals import decisions
from tradelab.strategies.registry import REGISTRY
from tests.helpers import make_panel


def test_screener_payload_is_strict_json():
    p = make_panel(n_days=600, n_names=4)
    data = screener_page.build_payload(p, years=1)
    json.dumps(data, allow_nan=False)  # raises on NaN / numpy types
    assert len(data["dates"]) == 252 and set(data["symbols"]) == {r["symbol"] for r in data["rows"]}
    assert all(len(v) == 252 for v in data["symbols"]["S1"].values())


def test_signal_day_payload_is_strict_json():
    p = make_panel(n_days=400, n_names=30)
    for d in decisions(REGISTRY["reversal_1w"], p, 20):
        json.dumps(_day(d), allow_nan=False)


def test_render_inlines_payload_and_shared_assets():
    html = render.render("signals.html", {"x": 1})
    assert "/*__" not in html and '"x":1' in html and "--surface" in html
