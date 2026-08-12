"""
Tests for backtesting/engine.py. test_execution_lag_prevents_lookahead is
the one that matters most - it doesn't just assert execution lag exists
in the abstract, it proves a signal at row t has zero effect on row t's
own return.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtesting.engine import (
    compute_metrics,
    compute_positions,
    generate_rsi_signals,
    run_backtest,
)


def make_df(closes: list[float], rsi_values: list[float | None]) -> pd.DataFrame:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    n = len(closes)
    returns = [None] + [
        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, n)
    ]
    return pd.DataFrame(
        {
            "event_timestamp": [start + timedelta(hours=i) for i in range(n)],
            "close": closes,
            "return_1h": returns,
            "rsi_14": rsi_values,
        }
    )


def test_generate_rsi_signals_buy_sell_hold():
    df = make_df(
        closes=[100, 100, 100, 100],
        rsi_values=[20, 50, 80, None],
    )
    result = generate_rsi_signals(df)
    assert list(result["signal"]) == ["BUY", "HOLD", "SELL", "HOLD"]


def test_position_opens_one_row_after_buy_signal():
    """This is execution lag in its most basic form: a BUY signal at
    row 1 must not open a position until row 2."""
    df = make_df(closes=[100] * 5, rsi_values=[50, 20, 50, 50, 50])
    df = generate_rsi_signals(df)
    df = compute_positions(df)

    assert df.loc[1, "signal"] == "BUY"
    assert df.loc[1, "position"] == 0  # NOT yet open on the signal row itself
    assert df.loc[2, "position"] == 1  # opens the row AFTER


def test_position_closes_one_row_after_sell_signal():
    df = make_df(closes=[100] * 6, rsi_values=[20, 50, 50, 80, 50, 50])
    df = generate_rsi_signals(df)
    df = compute_positions(df)

    assert df.loc[3, "signal"] == "SELL"
    assert df.loc[3, "position"] == 1  # still open on the signal row itself
    assert df.loc[4, "position"] == 0  # closes the row AFTER


def test_execution_lag_prevents_lookahead():
    """
    THE core test. Two runs, identical except for the price at the very
    last row (which only affects return_1h of that last row, and the
    signal of that row, via rsi_14 which we control directly here).
    Every position and strategy_return value for all EARLIER rows must
    be byte-for-byte identical - if changing only the future affected
    the past, that's look-ahead bias in the execution-timing logic.
    """
    rsi = [50, 20, 50, 50, 80, 50]

    df1 = make_df(closes=[100, 101, 102, 103, 104, 105], rsi_values=rsi)
    df1 = run_backtest(compute_positions(generate_rsi_signals(df1)))

    df2 = make_df(closes=[100, 101, 102, 103, 104, 999], rsi_values=rsi)
    df2 = run_backtest(compute_positions(generate_rsi_signals(df2)))

    for i in range(5):  # every row except the mutated last one
        assert df1.loc[i, "position"] == df2.loc[i, "position"]
        assert df1.loc[i, "strategy_return"] == pytest.approx(
            df2.loc[i, "strategy_return"]
        )


def test_fees_strictly_reduce_returns():
    df = make_df(closes=[100, 105, 110, 100, 95], rsi_values=[20, 50, 80, 50, 50])
    df = compute_positions(generate_rsi_signals(df))

    no_fee = run_backtest(df.copy(), fee_bps=0.0)
    with_fee = run_backtest(df.copy(), fee_bps=50.0)

    assert with_fee["equity"].iloc[-1] < no_fee["equity"].iloc[-1]


def test_flat_periods_contribute_zero_return():
    """If the strategy never signals BUY, it should just sit in cash -
    equity should never move, regardless of price action."""
    df = make_df(closes=[100, 150, 80, 200, 50], rsi_values=[50, 50, 50, 50, 50])
    df = run_backtest(compute_positions(generate_rsi_signals(df)))

    assert df["equity"].iloc[-1] == pytest.approx(1000.0)


def test_metrics_on_hand_calculable_example():
    """An always-long scenario: BUY signal at row 0 opens the position at
    row 1 (execution lag), and it stays open through row 2 since no SELL
    signal ever fires. Both period returns (row0->row1, row1->row2) are
    active, so equity should compound both: 1000 * 1.10 * 1.10 = 1210."""
    closes = [100, 110, 121]  # +10% each period
    df = make_df(closes=closes, rsi_values=[20, 50, 50])
    df = run_backtest(compute_positions(generate_rsi_signals(df)), fee_bps=0.0)
    metrics = compute_metrics(df)

    assert metrics["num_active_periods"] == 2
    assert df["equity"].iloc[-1] == pytest.approx(1000.0 * 1.10 * 1.10)


def test_max_drawdown_is_negative_or_zero():
    df = make_df(closes=[100, 90, 80, 100, 120], rsi_values=[20, 50, 50, 50, 50])
    df = run_backtest(compute_positions(generate_rsi_signals(df)))
    metrics = compute_metrics(df)

    assert metrics["max_drawdown"] <= 0
