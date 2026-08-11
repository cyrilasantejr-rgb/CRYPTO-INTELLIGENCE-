"""
Tests for features/market_features.py and features/market.py.

The test that matters most here is test_no_lookahead_bias - it doesn't
just assert a property in the abstract, it actually mutates a LATER row's
price and proves an EARLIER row's feature value is completely unaffected.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pyspark.sql import Row

from databricks.silver.market_silver import get_or_create_spark
from features.market import build_token_market_features
from features.market_features import add_returns, add_rsi, add_sma, add_volatility


@pytest.fixture(scope="module")
def spark():
    session = get_or_create_spark(app_name="test-market-features")
    yield session
    session.stop()


def make_series(
    spark, token: str, closes: list[float], token_address: str | None = None
):
    """Builds a simple hourly time series of rows for one token, given a
    list of close prices. open/high/low are set equal to close for
    simplicity - fine, since these tests are about the feature math, not
    OHLC relationships."""
    token_address = token_address or token
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rows = [
        Row(
            event_id=f"{token}-{i}",
            token_address=token_address,
            event_timestamp=start + timedelta(hours=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=100.0,
        )
        for i, c in enumerate(closes)
    ]
    return spark.createDataFrame(rows)


def test_returns_match_hand_calculation(spark):
    df = make_series(spark, "A", [100.0, 110.0, 121.0])
    result = add_returns(df, periods=[1]).orderBy("event_timestamp").collect()

    assert result[0].return_1h is None  # no prior row - correctly null, not 0
    assert result[1].return_1h == pytest.approx(0.10)  # (110-100)/100
    assert result[2].return_1h == pytest.approx(0.10)  # (121-110)/110


def test_sma_is_null_during_warmup(spark):
    """A 3-row SMA needs 3 rows. Rows 0 and 1 don't have enough history -
    they must be null, not a quietly-short average."""
    df = make_series(spark, "A", [10.0, 20.0, 30.0, 40.0])
    result = add_sma(df, window=3).orderBy("event_timestamp").collect()

    assert result[0].sma_3 is None
    assert result[1].sma_3 is None
    assert result[2].sma_3 == pytest.approx(20.0)  # avg(10,20,30)
    assert result[3].sma_3 == pytest.approx(30.0)  # avg(20,30,40)


def test_volatility_requires_returns_first(spark):
    df = make_series(spark, "A", [100.0, 105.0, 100.0, 110.0, 100.0])
    df = add_returns(df, periods=[1])
    result = add_volatility(df, window=4).orderBy("event_timestamp").collect()

    # row 0 has no return_1h (no prior row), so a FULL 4-value window of
    # return_1h isn't available until row 4 (rows 1,2,3,4 all have a
    # non-null return_1h - F.count() ignores nulls, so row 3's window
    # only contains 3 non-null values and is correctly still null).
    assert result[4].volatility_4 is not None
    assert result[3].volatility_4 is None


def test_rsi_bounded_between_0_and_100(spark):
    closes = [100 + (i % 5) - 2 for i in range(30)]  # oscillating series
    df = make_series(spark, "A", [float(c) for c in closes])
    result = add_rsi(df, window=14).orderBy("event_timestamp").collect()

    computed = [r.rsi_14 for r in result if r.rsi_14 is not None]
    assert len(computed) > 0
    assert all(0 <= v <= 100 for v in computed)


def test_no_lookahead_bias(spark):
    """
    THE core test. Compute return_1h and sma_3 for a series, note row 2's
    values. Then change ONLY a later row's price (row 4) and recompute.
    Row 2's feature values must be byte-for-byte identical - if they
    changed at all, that means a "future" row leaked into a "past" row's
    feature, which is exactly the bug this whole module is designed to
    prevent.
    """
    original = make_series(spark, "A", [100.0, 102.0, 101.0, 103.0, 99.0])
    df1 = add_sma(add_returns(original, periods=[1]), window=3)
    row2_before = df1.orderBy("event_timestamp").collect()[2]

    mutated = make_series(
        spark, "A", [100.0, 102.0, 101.0, 103.0, 500.0]
    )  # row 4 changed
    df2 = add_sma(add_returns(mutated, periods=[1]), window=3)
    row2_after = df2.orderBy("event_timestamp").collect()[2]

    assert row2_before.return_1h == row2_after.return_1h
    assert row2_before.sma_3 == row2_after.sma_3


def test_ema_and_macd_present_and_bounded(spark):
    closes = [100.0 + i * 0.5 for i in range(40)]  # steady uptrend
    df = make_series(spark, "A", closes)
    result = build_token_market_features(df).orderBy("event_timestamp").collect()

    last_row = result[-1]
    assert last_row.ema_fast is not None
    assert last_row.ema_slow is not None
    assert last_row.macd is not None
    # In a steady uptrend, the fast EMA should be above the slow EMA
    assert last_row.ema_fast > last_row.ema_slow


def test_features_are_independent_per_token(spark):
    """Token A's features must never be influenced by Token B's rows, even
    though they share the same underlying DataFrame and Window spec."""
    df_a = make_series(spark, "A", [100.0, 110.0, 120.0])
    df_b = make_series(spark, "B", [1.0, 1.0, 1.0])
    combined = df_a.union(df_b)

    result = add_returns(combined, periods=[1]).collect()
    b_returns = [
        r.return_1h
        for r in result
        if r.token_address == "B" and r.return_1h is not None
    ]

    assert all(r == 0.0 for r in b_returns)  # B's flat prices, unaffected by A
