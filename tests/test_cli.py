import datetime as dt

from tradelab.cli import parser, signals_text
from tradelab.live.signals import Decision, Position, Trade

D = dt.date


def test_parser_routes_commands():
    a = parser().parse_args(
        [
            "portfolio",
            "add",
            "buy",
            "NVDA",
            "10",
            "229.5",
            "--strategy",
            "rsi2_reversion",
        ]
    )
    assert a.action == "add" and a.qty == 10 and a.fn.__name__ == "cmd_portfolio"
    assert parser().parse_args(["dashboard", "all", "--open"]).page == "all"
    assert parser().parse_args(["daily", "--no-fetch"]).no_fetch


def test_signals_text_groups_actions_and_quiet_days():
    pos = (Position("AAA", 0.1, 5.0, 10.0),)
    by = {
        "rsi2_reversion": [
            Decision(
                D(2026, 10, 9),
                True,
                D(2026, 10, 9),
                D(2026, 10, 12),
                pos,
                (
                    Trade("AAA", "BUY", 0, 0.1, 5.0, 10.0),
                    Trade("BBB", "SELL", 0.1, 0, 80.0, 20.0),
                ),
            )
        ],
        "momentum_12_1": [
            Decision(D(2026, 10, 9), False, D(2026, 9, 30), D(2026, 10, 30), pos, ())
        ],
        "qqq_50_200_trend": [
            Decision(D(2026, 10, 9), True, D(2026, 10, 9), D(2026, 10, 12), pos, ())
        ],
    }
    text = signals_text(by, "2026-10-09", "2026-10-12")
    assert "REBALANCE -> BUY AAA; SELL BBB" in text
    assert "no trade; 1 positions; next rebalance 2026-10-30" in text
    assert "checked, no changes" in text and "not investment advice" in text
