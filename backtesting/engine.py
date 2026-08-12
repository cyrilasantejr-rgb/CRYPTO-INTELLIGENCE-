"""
Backtesting engine: simulates how a strategy would have actually traded
against historical Gold features, with realistic execution timing and
trading costs.

EXECUTION LAG - the single most important rule in this file.

A signal computed from row t's close (e.g. "RSI at candle t says buy")
cannot be traded at candle t's own open or close - in the real world you
don't know a candle's close until it's over, and even if you did, you
can't submit and fill an order instantaneously at that exact price.
Every function below enforces "signal at t -> position effective at
t+1" by construction: `compute_positions()` shifts the signal-derived
position forward by one row before it's ever multiplied against a
return. See test_execution_lag_prevents_lookahead in
tests/unit/test_backtest_engine.py for a concrete proof, not just an
assertion.

WHY PANDAS, NOT SPARK (see ADR-011 in docs/decisions.md).
An equity curve compounds sequentially - today's capital depends on
yesterday's capital - the same recursive shape as EMA (Phase 4), which
is exactly why Spark's window functions don't fit here either. This
runs on an already-aggregated per-token time series small enough to
comfortably live in memory.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 24 * 365  # for annualizing Sharpe/Sortino on hourly data


def generate_rsi_signals(
    df: pd.DataFrame, oversold: float = 30.0, overbought: float = 70.0
) -> pd.DataFrame:
    """
    Placeholder rule-based strategy: BUY when RSI indicates oversold,
    SELL when overbought, otherwise HOLD. This is intentionally simple -
    Phase 6 replaces this with actual ML model predictions, but the
    backtest engine below doesn't care where a signal came from, only
    that it's a per-row BUY/SELL/HOLD column. That decoupling is
    deliberate: this engine will backtest ML signals with zero changes.
    """
    df = df.copy()
    conditions = [df["rsi_14"] < oversold, df["rsi_14"] > overbought]
    choices = ["BUY", "SELL"]
    df["signal"] = np.select(conditions, choices, default="HOLD")
    df.loc[df["rsi_14"].isna(), "signal"] = "HOLD"
    return df


def compute_positions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Turns a signal column into a position column (0 = flat, 1 = long),
    enforcing execution lag: a BUY/SELL signal observed at row t only
    takes effect starting at row t+1.

    Position logic (long-only, for simplicity): once flat, a BUY signal
    opens a long position; once long, a SELL signal closes it back to
    flat; a HOLD signal (or a BUY while already long, or a SELL while
    already flat) carries the previous position forward unchanged.

    Implemented as an explicit sequential loop rather than a vectorized
    pandas operation - because position state is itself recursive
    (today's position depends on yesterday's position AND today's
    signal), the same reason EMA needed a pandas UDF instead of a
    window function in Phase 4. At our current scale (one token, a few
    hundred rows) a plain Python loop is fast and, more importantly,
    obviously correct - not worth obscuring behind a clever vectorized
    trick. At 10,000+ tokens with years of history, this loop would
    become the actual bottleneck; the fix at that scale is running it
    per-token in parallel (e.g. via multiprocessing or a Spark
    applyInPandas grouped by token_address, exactly like Phase 4's
    add_ema_and_macd) rather than trying to vectorize the sequential
    logic itself away.
    """
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    intended_position = []
    current = 0
    for signal in df["signal"]:
        if current == 0 and signal == "BUY":
            current = 1
        elif current == 1 and signal == "SELL":
            current = 0
        intended_position.append(current)

    df["intended_position"] = intended_position
    # THE execution-lag guard: shift by one row before this position is
    # ever used to compute a return. Row t's intended_position (decided
    # from row t's signal) only takes effect starting row t+1.
    df["position"] = df["intended_position"].shift(1).fillna(0).astype(int)
    return df


def run_backtest(
    df: pd.DataFrame,
    fee_bps: float = 10.0,
    initial_capital: float = 1000.0,
) -> pd.DataFrame:
    """
    Computes a cost-aware equity curve. Requires `position` (from
    compute_positions) and `return_1h` (from Phase 4's add_returns)
    already present.

    fee_bps: round-trip-equivalent cost in basis points (1 bps = 0.01%)
    charged whenever the position changes - covers both trading fees
    and a simple slippage allowance in one number, since separating
    them precisely needs order-book data we don't have.
    """
    df = df.copy()
    df["position_changed"] = df["position"] != df["position"].shift(1).fillna(0)
    fee_rate = fee_bps / 10_000
    df["cost"] = np.where(df["position_changed"], fee_rate, 0.0)

    # Only earn/lose the period's return while actually holding a
    # position - a flat period contributes zero strategy return.
    df["strategy_return"] = (df["position"] * df["return_1h"].fillna(0)) - df["cost"]

    df["equity"] = initial_capital * (1 + df["strategy_return"]).cumprod()
    df["buy_hold_equity"] = initial_capital * (1 + df["return_1h"].fillna(0)).cumprod()

    return df


def compute_metrics(backtest_df: pd.DataFrame, initial_capital: float = 1000.0) -> dict:
    """
    Standard performance metrics. Per the project's own stated
    principle, a strategy is judged on these - never on classification
    accuracy alone.
    """
    returns = backtest_df["strategy_return"]
    active_returns = returns[backtest_df["position"] == 1]

    total_return = backtest_df["equity"].iloc[-1] / initial_capital - 1
    buy_hold_return = backtest_df["buy_hold_equity"].iloc[-1] / initial_capital - 1

    mean_return = returns.mean()
    std_return = returns.std()
    sharpe = (
        (mean_return / std_return) * np.sqrt(HOURS_PER_YEAR)
        if std_return and std_return > 0
        else 0.0
    )

    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std()
    sortino = (
        (mean_return / downside_std) * np.sqrt(HOURS_PER_YEAR)
        if downside_std and downside_std > 0
        else 0.0
    )

    running_max = backtest_df["equity"].cummax()
    drawdown = (backtest_df["equity"] - running_max) / running_max
    max_drawdown = drawdown.min()

    wins = active_returns[active_returns > 0]
    losses = active_returns[active_returns < 0]
    win_rate = len(wins) / len(active_returns) if len(active_returns) > 0 else 0.0
    profit_factor = (
        wins.sum() / abs(losses.sum())
        if len(losses) > 0 and losses.sum() != 0
        else float("inf") if len(wins) > 0 else 0.0
    )

    return {
        "total_return": total_return,
        "buy_hold_return": buy_hold_return,
        "return_vs_buy_hold": total_return - buy_hold_return,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expected_value_per_period": mean_return,
        "num_active_periods": len(active_returns),
    }
