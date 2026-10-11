import pytest

from tradelab.live.suggestions import SizingRules, suggest

PRICES = {"A": 100.0, "B": 50.0, "C": 20.0}
SCORES = {"B": 0.45, "C": -0.1}
RULES = SizingRules(capital_usd=2000.0, min_order_usd=100.0)


def run(targets, held, rebalance=True, cash=None, rules=RULES):
    orders = suggest(
        targets,
        held,
        PRICES,
        SCORES,
        rules,
        rebalance,
        "12-1 return",
        "return",
        cash_available=cash,
    )
    return {o.symbol: (o.action, o.qty) for o in orders}, orders


def test_rebalance_day_resizes_and_exits():
    # A already holds 10 shares = 50% of $2000, so only B and C trade
    got, orders = run({"A": 0.5, "B": 0.5}, {"A": 10, "C": 5})
    assert got == {"C": ("SELL", 5), "B": ("BUY", 20)}
    assert orders[0].side == "SELL"
    assert "enters the target at 50.0%" in orders[1].reason
    assert "+45.0%" in orders[1].reason


def test_off_schedule_only_membership_changes():
    # A is under its target but already held: no resizing between rebalances
    got, _ = run({"A": 0.9, "B": 0.1}, {"A": 2}, rebalance=False)
    assert got == {"B": ("BUY", 4)}


def test_small_orders_skipped_but_full_exit_always_sent():
    # +0.4 share of A = $40, under the $100 minimum
    got, _ = run({"A": 0.52, "B": 0.0}, {"A": 10, "B": 1})
    assert got == {"B": ("SELL", 1)}


def test_trim_on_rebalance():
    got, _ = run({"A": 0.25}, {"A": 10})
    assert got == {"A": ("TRIM", 5)}


def test_buys_scaled_to_available_cash():
    # $200 cash + $100 from selling C = $300 of the $2000 wanted for B (15%)
    got, orders = run({"B": 1.0}, {"C": 5}, cash=200.0)
    assert got == {"C": ("SELL", 5), "B": ("BUY", 6)}
    assert "scaled to 15%" in orders[1].reason


def test_fractional_shares():
    rules = SizingRules(capital_usd=1000.0, min_order_usd=10.0, fractional=True)
    got, _ = run({"A": 0.333}, {}, rules=rules)
    assert got["A"][1] == pytest.approx(3.33)
