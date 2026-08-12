"""
Tests for ml/entry/train.py. Uses a synthetic dataset large enough for
logistic regression to actually converge, with a genuinely learnable
signal (label correlates with one feature), so these tests prove the
pipeline works end-to-end - not just that it runs without crashing.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ml.entry.split import chronological_split
from ml.entry.train import FEATURE_COLUMNS, prepare_training_data, train_entry_model


def make_learnable_dataset(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Synthetic features + a label that's genuinely predictable from
    rsi_14 (label=1 when rsi_14 is low, mimicking a real oversold-bounce
    pattern), so a working model should score meaningfully better than
    a coin flip on this data."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)

    rsi = rng.uniform(0, 100, n)
    label = (rsi < 35).astype(float)
    # add a bit of noise so it's not a trivial perfect separator
    flip_mask = rng.random(n) < 0.1
    label[flip_mask] = 1 - label[flip_mask]

    data = {col: rng.normal(0, 1, n) for col in FEATURE_COLUMNS}
    data["rsi_14"] = rsi
    data["label"] = label
    data["event_timestamp"] = [start + timedelta(hours=i) for i in range(n)]
    return pd.DataFrame(data)


def test_prepare_training_data_drops_nulls():
    df = make_learnable_dataset(50)
    df.loc[0, "rsi_14"] = np.nan
    df.loc[1, "label"] = np.nan

    prepared = prepare_training_data(df)
    assert len(prepared) == 48


def test_model_trains_and_returns_metrics():
    df = make_learnable_dataset(200)
    train_df, test_df = chronological_split(df, test_fraction=0.3)

    result = train_entry_model(train_df, test_df)

    assert result.n_train == 140
    assert result.n_test == 60
    assert 0 <= result.metrics["precision"] <= 1
    assert 0 <= result.metrics["recall"] <= 1
    assert 0 <= result.metrics["brier_score"] <= 1


def test_model_beats_random_on_learnable_data():
    """The core sanity check: on data with a genuinely learnable
    pattern, the model's ROC-AUC should be meaningfully above 0.5
    (random guessing). If this fails, something is broken in the
    training pipeline, not just underpowered by small/noisy data."""
    df = make_learnable_dataset(300)
    train_df, test_df = chronological_split(df, test_fraction=0.3)

    result = train_entry_model(train_df, test_df)

    assert result.metrics["roc_auc"] is not None
    assert result.metrics["roc_auc"] > 0.65


def test_scaler_is_fit_only_on_train_data():
    """A subtle leakage point: if the scaler were fit on train+test
    combined, test-set statistics would leak into feature scaling.
    This checks the scaler's learned mean matches train data only."""
    df = make_learnable_dataset(100)
    train_df, test_df = chronological_split(df, test_fraction=0.2)

    result = train_entry_model(train_df, test_df)

    expected_mean = train_df[FEATURE_COLUMNS].mean().to_numpy()
    np.testing.assert_allclose(result.scaler.mean_, expected_mean, rtol=1e-6)
