"""
Tests for ml/exit/labels.py and ml/exit/train.py.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ml.entry.split import chronological_split
from ml.exit.labels import exit_label
from ml.exit.train import (
    EXIT_FEATURE_COLUMNS,
    add_simulated_position_features,
    prepare_exit_training_data,
    train_exit_model,
)


def make_price_df(closes: list[float]) -> pd.DataFrame:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "event_timestamp": [start + timedelta(hours=i) for i in range(len(closes))],
            "close": closes,
        }
    )


def test_exit_label_fires_on_decline():
    df = make_price_df([100, 100, 85, 100])  # 15% drop at row 2
    labels = exit_label(df, horizon=2, decline_threshold=0.10)
    assert labels.iloc[0] == 1.0  # sees the drop within horizon


def test_exit_label_does_not_fire_on_small_dip():
    df = make_price_df([100, 100, 97, 100])  # only 3% drop
    labels = exit_label(df, horizon=2, decline_threshold=0.10)
    assert labels.iloc[0] == 0.0


def test_exit_label_nan_without_full_horizon():
    df = make_price_df([100, 99])
    labels = exit_label(df, horizon=3, decline_threshold=0.10)
    assert labels.isna().all()


def test_simulated_position_features_computed_correctly():
    closes = [100, 100, 100, 100, 90]  # 10% drop by row 4, entry at row 0
    df = make_price_df(closes)
    result = add_simulated_position_features(df, holding_period=4)

    # Row 4: entry was row 0 (close=100), current close=90
    assert result.loc[4, "unrealized_return"] == pytest.approx(-0.10)
    assert result.loc[4, "periods_held"] == 4


def test_simulated_position_nan_before_holding_period_elapsed():
    df = make_price_df([100, 101, 102])
    result = add_simulated_position_features(df, holding_period=4)
    # Not enough history yet for a 4-period-ago entry - must be NaN.
    assert result["unrealized_return"].isna().all()


def _make_learnable_exit_dataset(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)

    unrealized_return = rng.normal(0, 0.05, n)
    # Exit label correlates with being deep in a loss - a learnable pattern
    exit_lbl = (unrealized_return < -0.03).astype(float)
    flip = rng.random(n) < 0.1
    exit_lbl[flip] = 1 - exit_lbl[flip]

    data = {col: rng.normal(0, 1, n) for col in EXIT_FEATURE_COLUMNS}
    data["unrealized_return"] = unrealized_return
    data["exit_label"] = exit_lbl
    data["event_timestamp"] = [start + timedelta(hours=i) for i in range(n)]
    return pd.DataFrame(data)


def test_exit_model_trains_and_evaluates():
    df = _make_learnable_exit_dataset(200)
    train_df, test_df = chronological_split(df, test_fraction=0.3)

    result = train_exit_model(train_df, test_df)

    assert result.n_train == 140
    assert result.n_test == 60
    assert 0 <= result.metrics["precision"] <= 1


def test_exit_model_beats_random_on_learnable_data():
    df = _make_learnable_exit_dataset(300)
    train_df, test_df = chronological_split(df, test_fraction=0.3)

    result = train_exit_model(train_df, test_df)

    assert result.metrics["roc_auc"] is not None
    assert result.metrics["roc_auc"] > 0.65


def test_prepare_exit_training_data_drops_nulls():
    df = _make_learnable_exit_dataset(50)
    df.loc[0, "exit_label"] = np.nan
    prepared = prepare_exit_training_data(df)
    assert len(prepared) == 49
