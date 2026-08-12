"""
Tests for ml/entry/labels.py.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from ml.entry.labels import triple_barrier_label


def make_price_df(closes: list[float]) -> pd.DataFrame:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "event_timestamp": [start + timedelta(hours=i) for i in range(len(closes))],
            "close": closes,
        }
    )


def test_upper_barrier_hit_labels_one():
    # Entry at 100, upper barrier at 105 (5%), lower at 95 (5%).
    # Row 1 hits 106 first - upper barrier wins.
    df = make_price_df([100, 106, 90])
    labels = triple_barrier_label(df, horizon=2, upper_pct=0.05, lower_pct=0.05)
    assert labels.iloc[0] == 1.0


def test_lower_barrier_hit_labels_zero():
    df = make_price_df([100, 94, 110])
    labels = triple_barrier_label(df, horizon=2, upper_pct=0.05, lower_pct=0.05)
    assert labels.iloc[0] == 0.0


def test_neither_barrier_hit_within_horizon_labels_zero():
    # Prices stay flat within +-5%, never clearly breaking out.
    df = make_price_df([100, 101, 102, 103])
    labels = triple_barrier_label(df, horizon=3, upper_pct=0.05, lower_pct=0.05)
    assert labels.iloc[0] == 0.0


def test_first_barrier_touched_wins_not_last():
    # Row 1 touches lower barrier (95), row 2 recovers past upper (106).
    # The LOWER barrier was touched FIRST - label must be 0, not 1.
    df = make_price_df([100, 94, 110])
    labels = triple_barrier_label(df, horizon=2, upper_pct=0.05, lower_pct=0.05)
    assert labels.iloc[0] == 0.0


def test_insufficient_future_data_is_nan_not_guessed():
    """The last few rows of any series can't have a full horizon of
    future data - their label must be NaN, never a silently-guessed 0."""
    df = make_price_df([100, 101, 102])
    labels = triple_barrier_label(df, horizon=5, upper_pct=0.05, lower_pct=0.05)
    assert labels.isna().all()


def test_labels_do_not_depend_on_data_beyond_horizon():
    """Proves the horizon boundary is respected: a huge price move
    happening AFTER the horizon window must not affect the label."""
    within_horizon = [100, 101, 102]  # flat, no barrier hit
    df1 = make_price_df(within_horizon + [1000])  # huge jump after horizon
    df2 = make_price_df(within_horizon + [1])  # huge crash after horizon

    labels1 = triple_barrier_label(df1, horizon=2, upper_pct=0.05, lower_pct=0.05)
    labels2 = triple_barrier_label(df2, horizon=2, upper_pct=0.05, lower_pct=0.05)

    assert labels1.iloc[0] == labels2.iloc[0] == 0.0


def test_returns_correct_length_and_index():
    df = make_price_df([100, 101, 102, 103, 104])
    labels = triple_barrier_label(df, horizon=2, upper_pct=0.05, lower_pct=0.05)
    assert len(labels) == len(df)
