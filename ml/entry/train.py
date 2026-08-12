"""
Entry model: baseline logistic regression.

Answers "given current market features, what's the probability price
rises to the upper barrier before the lower barrier within the horizon?"
- exactly the framing your project's own spec calls for
("Probability +X% occurs before -Y% during horizon H"), and deliberately
NOT a raw price-prediction model.

A baseline model exists before anything fancier, per the project's own
stated principle: logistic regression here, random forest/XGBoost later
once there's a reason to believe the extra complexity earns its keep.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = [
    "return_1h",
    "return_4h",
    "return_24h",
    "sma_20",
    "volatility_20",
    "rsi_14",
    "ema_fast",
    "ema_slow",
    "macd",
    "macd_signal",
]


@dataclass
class EntryModelResult:
    model: LogisticRegression
    scaler: StandardScaler
    metrics: dict
    n_train: int
    n_test: int


def prepare_training_data(
    df: pd.DataFrame, feature_columns: list[str] = FEATURE_COLUMNS
) -> pd.DataFrame:
    """
    Drops rows with any null feature or null label. Nulls here are
    EXPECTED and CORRECT, not bugs - they're warm-up rows from Phase 4
    (not enough history for a full-window feature yet) and end-of-series
    rows from the labeling step (not enough future data for a full
    horizon yet). Silently imputing them would hide real gaps in what
    the model can legitimately know; dropping them is the honest choice.
    """
    required = feature_columns + ["label"]
    return df.dropna(subset=required).reset_index(drop=True)


def train_entry_model(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    feature_columns: list[str] = FEATURE_COLUMNS,
) -> EntryModelResult:
    """
    Trains a logistic regression on `train_df`, evaluates on `test_df`.
    Caller is responsible for ensuring train/test came from
    chronological_split() - this function has no way to enforce that
    itself, since by the time it receives two DataFrames the temporal
    relationship between them is no longer visible to it.
    """
    X_train = train_df[feature_columns].to_numpy()
    y_train = train_df["label"].to_numpy()
    X_test = test_df[feature_columns].to_numpy()
    y_test = test_df["label"].to_numpy()

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train_scaled, y_train)

    y_pred = model.predict(X_test_scaled)
    y_proba = model.predict_proba(X_test_scaled)[:, 1]

    metrics = {
        "precision": precision_score(y_test, y_pred, zero_division=0),
        "recall": recall_score(y_test, y_pred, zero_division=0),
        "f1": f1_score(y_test, y_pred, zero_division=0),
        # ROC-AUC is undefined with only one class present in y_test -
        # a real possibility with a small/imbalanced dataset like ours.
        "roc_auc": (
            roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else None
        ),
        "brier_score": brier_score_loss(y_test, y_proba),
        "positive_rate_train": float(np.mean(y_train)),
        "positive_rate_test": float(np.mean(y_test)),
    }

    return EntryModelResult(
        model=model,
        scaler=scaler,
        metrics=metrics,
        n_train=len(train_df),
        n_test=len(test_df),
    )
