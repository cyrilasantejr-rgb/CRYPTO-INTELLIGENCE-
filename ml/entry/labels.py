"""
Triple-barrier labeling for the entry model.

CRITICAL DISTINCTION: labels vs features.

Every label produced here is deliberately forward-looking - that's what
a label IS. For row t, we look at rows t+1 through t+horizon to see
whether price rose to the upper barrier or fell to the lower barrier
first. This is not a bug and not the look-ahead-bias problem from
Phase 4 - it's the entire point of supervised learning: "given what I
knew at time t (the features), what actually happened afterward (the
label)?"

The rule that must never be violated is a DIFFERENT one: the FEATURES
used to predict this label (RSI, MACD, volatility, etc. from Phase 4)
must only use information available AT OR BEFORE row t. Phase 4's
test_no_lookahead_bias already proves that for the features. This
module's job is only to compute labels correctly - it never touches
feature columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier_label(
    df: pd.DataFrame,
    horizon: int,
    upper_pct: float,
    lower_pct: float,
    price_col: str = "close",
) -> pd.Series:
    """
    For each row t, looks forward up to `horizon` rows (same token,
    ordered by time) and labels:
        1  if price rises to close[t] * (1 + upper_pct) before falling
           to close[t] * (1 - lower_pct)
        0  if the lower barrier is hit first
        0  if neither barrier is hit within the horizon (a conservative
           default - "didn't clearly win" counts as a non-entry)
        NaN if there aren't `horizon` rows of future data left (the
           label is genuinely unknowable, not silently wrong - same
           null-not-guessed principle as Phase 4's warm-up handling)

    Must be called per-token (or with a DataFrame already containing
    only one token) - it assumes `df` is sorted chronologically for a
    single token's price series.
    """
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    prices = df[price_col].to_numpy()
    n = len(prices)
    labels = np.full(n, np.nan)

    for t in range(n):
        if t + horizon >= n:
            continue  # not enough future data - label stays NaN

        entry_price = prices[t]
        upper_barrier = entry_price * (1 + upper_pct)
        lower_barrier = entry_price * (1 - lower_pct)

        label = 0  # default: neither barrier hit within horizon
        for future_i in range(t + 1, t + horizon + 1):
            price = prices[future_i]
            if price >= upper_barrier:
                label = 1
                break
            if price <= lower_barrier:
                label = 0
                break

        labels[t] = label

    return pd.Series(labels, index=df.index, name="label")
