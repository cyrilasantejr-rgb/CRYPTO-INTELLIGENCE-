"""
Exit model: baseline logistic regression using SIMULATED position
features.

IMPORTANT LIMITATION - stated plainly, not hidden.

Per ADR-005 (docs/decisions.md), the entry and exit models must stay
conceptually separate, and the exit model's real inputs should include
position-aware data: entry price, unrealized P&L, time-in-trade,
highest-price-since-entry. That data comes from a real position
tracker, which doesn't exist until Phase 13 (paper trading engine).

Building a fake position tracker just to satisfy this phase's checklist
would be worse than being upfront: this module SIMULATES a fixed-length
hypothetical holding period (assume a position was opened
`holding_period` rows ago) to construct approximate position-aware
features from market data alone. This lets us build and test the
correct model ARCHITECTURE and the correct evaluation pipeline now.
When Phase 13 lands, `add_simulated_position_features` gets replaced by
real position data pulled from the position manager - the model
training code (train_exit_model) does not change, since it only cares
about column names, not where they came from.
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

EXIT_FEATURE_COLUMNS = [
    "unrealized_return",
    "periods_held",
    "drawdown_from_high",
    "volatility_20",
    "rsi_14",
    "macd",
]


def add_simulated_position_features(
    df: pd.DataFrame, holding_period: int = 4
) -> pd.DataFrame:
    """
    Approximates position-aware features by assuming a hypothetical
    entry `holding_period` rows before each row. See module docstring
    for why this is a deliberate stand-in, not real position data.
    """
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    entry_price = df["close"].shift(holding_period)

    df["unrealized_return"] = (df["close"] - entry_price) / entry_price
    df["periods_held"] = holding_period

    rolling_high = df["close"].rolling(window=holding_period, min_periods=1).max()
    df["drawdown_from_high"] = (df["close"] - rolling_high) / rolling_high

    return df


@dataclass
class ExitModelResult:
    model: LogisticRegression
    scaler: StandardScaler
    metrics: dict
    n_train: int
    n_test: int


def prepare_exit_training_data(
    df: pd.DataFrame, feature_columns: list[str] = EXIT_FEATURE_COLUMNS
) -> pd.DataFrame:
    required = feature_columns + ["exit_label"]
    return df.dropna(subset=required).reset_index(drop=True)


def train_exit_model(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    feature_columns: list[str] = EXIT_FEATURE_COLUMNS,
) -> ExitModelResult:
    """
    Predicts `exit_label` (1 = should exit now, 0 = should hold) from
    simulated position-aware features. Structurally identical training
    logic to ml/entry/train.py, deliberately - a separate model, same
    disciplined process (chronological data in, scaler fit on train
    only, full evaluation suite out).
    """
    X_train = train_df[feature_columns].to_numpy()
    y_train = train_df["exit_label"].to_numpy()
    X_test = test_df[feature_columns].to_numpy()
    y_test = test_df["exit_label"].to_numpy()

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
        "roc_auc": (
            roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else None
        ),
        "brier_score": brier_score_loss(y_test, y_proba),
        "positive_rate_train": float(np.mean(y_train)),
        "positive_rate_test": float(np.mean(y_test)),
    }

    return ExitModelResult(
        model=model,
        scaler=scaler,
        metrics=metrics,
        n_train=len(train_df),
        n_test=len(test_df),
    )
