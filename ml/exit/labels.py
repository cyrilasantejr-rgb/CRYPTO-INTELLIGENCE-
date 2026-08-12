"""
Exit label generation.

Same forward-looking-by-design principle as ml/entry/labels.py: an exit
label answers "given a hypothetical open position at row t, should you
have exited by row t?" - which requires looking at what price actually
did afterward. This is legitimate for a LABEL; it would not be
legitimate for a FEATURE.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def exit_label(
    df: pd.DataFrame, horizon: int, decline_threshold: float, price_col: str = "close"
) -> pd.Series:
    """
    Labels 1 ("should exit now") if price falls by more than
    `decline_threshold` at any point within the next `horizon` rows,
    else 0 ("keep holding"). NaN when there isn't a full horizon of
    future data left - same "don't guess, say unknown" principle used
    throughout this project.
    """
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    prices = df[price_col].to_numpy()
    n = len(prices)
    labels = np.full(n, np.nan)

    for t in range(n):
        if t + horizon >= n:
            continue

        current_price = prices[t]
        future_window = prices[t + 1 : t + horizon + 1]
        min_future = future_window.min()
        decline = (min_future - current_price) / current_price

        labels[t] = 1.0 if decline <= -decline_threshold else 0.0

    return pd.Series(labels, index=df.index, name="exit_label")
