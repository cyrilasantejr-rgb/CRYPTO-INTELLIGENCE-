"""
Tests for ml/entry/split.py.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from ml.entry.split import chronological_split


def make_df(n: int) -> pd.DataFrame:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "event_timestamp": [start + timedelta(hours=i) for i in range(n)],
            "value": list(range(n)),
        }
    )


def test_split_respects_fraction():
    df = make_df(100)
    train, test = chronological_split(df, test_fraction=0.2)
    assert len(train) == 80
    assert len(test) == 20


def test_no_overlap_and_correct_order():
    """The core guarantee: every train timestamp is strictly earlier
    than every test timestamp - never a random shuffle."""
    df = make_df(50)
    train, test = chronological_split(df, test_fraction=0.3)

    assert train["event_timestamp"].max() < test["event_timestamp"].min()


def test_split_works_on_unsorted_input():
    """The function must sort internally - it shouldn't silently
    misbehave if handed data that isn't already time-ordered."""
    df = make_df(20)
    shuffled = df.sample(frac=1, random_state=42).reset_index(drop=True)

    train, test = chronological_split(shuffled, test_fraction=0.25)
    assert train["event_timestamp"].max() < test["event_timestamp"].min()


def test_all_rows_accounted_for():
    df = make_df(37)
    train, test = chronological_split(df, test_fraction=0.2)
    assert len(train) + len(test) == len(df)
