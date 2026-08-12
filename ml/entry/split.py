"""
Chronological train/test splitting for time-series ML.

WHY NOT sklearn's train_test_split(shuffle=True) (the default): a random
shuffle would let the model train on rows from AFTER the test period and
be evaluated on rows from BEFORE it - meaning the model could effectively
"see the future" relative to some of its own test data. This is a
distinct failure mode from feature look-ahead bias (Phase 4) and label
leakage (ml/entry/labels.py) - it's leakage through the SPLIT itself,
not through any individual row's content.

The only valid split for time-series is chronological: train on the
past, test on the future, with a strict boundary.
"""

from __future__ import annotations

import pandas as pd


def chronological_split(
    df: pd.DataFrame, test_fraction: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Splits a time-sorted DataFrame into (train, test) using only a
    cutoff timestamp - never a random shuffle. Every row in `train` has
    event_timestamp strictly earlier than every row in `test`.
    """
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    split_idx = int(len(df) * (1 - test_fraction))
    train = df.iloc[:split_idx].reset_index(drop=True)
    test = df.iloc[split_idx:].reset_index(drop=True)
    return train, test
