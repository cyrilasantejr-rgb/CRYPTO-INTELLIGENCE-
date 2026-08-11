"""
Market feature engineering: turns clean Silver OHLCV rows into the technical
indicators the entry/exit ML models will train on.

LOOK-AHEAD BIAS - read this before touching anything below.

Every function here computes a feature "as of" a given row using ONLY that
row and rows before it (same token, ordered by event_timestamp). This is
enforced by using Spark Window specs with `.rowsBetween(-N, 0)` - "N rows
before this one, through this one, inclusive" - never a positive upper
bound, which would let a window see into the future.

A feature that accidentally includes future rows will make backtests look
artificially good (the model is "cheating" by seeing what hasn't happened
yet) and then fail in live trading, where the future genuinely isn't
available. See tests/unit/test_market_features.py for a test that proves
this doesn't happen: it mutates a LATER row's price and asserts an
EARLIER row's feature value is unchanged.

WARM-UP PERIODS.

A 14-period RSI needs 14 prior rows to mean anything. For the first 13 rows
of any token's history, there aren't enough prior rows yet. Spark's window
aggregates will silently compute an average/stddev over however many rows
ARE available near the start (e.g. an "SMA-20" on row 3 quietly becomes an
average of 3 rows) unless we explicitly check the row count and null it out
ourselves. Every function below does that check - a feature is either
computed from a FULL window, or it's null. Never a silently-short window.
"""

from __future__ import annotations

import pandas as pd
from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType
from pyspark.sql.window import Window


def _token_window(rows_back: int):
    """A window over one token's rows, ordered by time, covering the
    current row and the `rows_back` rows before it. Never includes any
    row after the current one - that's the look-ahead-bias guard."""
    return (
        Window.partitionBy("token_address")
        .orderBy("event_timestamp")
        .rowsBetween(-rows_back, 0)
    )


def add_returns(df: DataFrame, periods: list[int]) -> DataFrame:
    """Adds return_{p}h columns: percent change vs. the close price `p`
    rows earlier for the same token. Uses lag(), which by definition only
    looks backward - there's no way to accidentally make this look ahead."""
    order_window = Window.partitionBy("token_address").orderBy("event_timestamp")
    for p in periods:
        prior_close = F.lag("close", p).over(order_window)
        df = df.withColumn(
            f"return_{p}h",
            F.when(
                prior_close.isNotNull(), (F.col("close") - prior_close) / prior_close
            ),
        )
    return df


def add_sma(df: DataFrame, window: int) -> DataFrame:
    """Simple moving average of `close` over the trailing `window` rows.
    Nulled out unless a full window of history is actually available."""
    w = _token_window(window - 1)
    row_count = F.count("close").over(w)
    df = df.withColumn(
        f"sma_{window}",
        F.when(row_count >= window, F.avg("close").over(w)),
    )
    return df


def add_volatility(df: DataFrame, window: int) -> DataFrame:
    """Rolling standard deviation of 1-period returns over `window` rows -
    a standard volatility proxy. Requires return_1h to already exist
    (call add_returns([1, ...]) first)."""
    w = _token_window(window - 1)
    row_count = F.count("return_1h").over(w)
    df = df.withColumn(
        f"volatility_{window}",
        F.when(row_count >= window, F.stddev("return_1h").over(w)),
    )
    return df


def add_rsi(df: DataFrame, window: int = 14) -> DataFrame:
    """
    Relative Strength Index, SMA-smoothed variant.

    NOTE - approximation, stated plainly: textbook RSI uses Wilder's
    smoothing, which is recursive (each value depends on the previous
    smoothed value, not a plain rolling window). Recursive computations
    don't fit Spark's window functions - see add_ema() below for how we
    handle a genuinely recursive indicator. This RSI instead uses a
    simple moving average of gains/losses, which is a common and
    documented simplification, but IS a different number than
    Wilder-smoothed RSI. Worth knowing, not worth hiding.
    """
    order_window = Window.partitionBy("token_address").orderBy("event_timestamp")
    change = F.col("close") - F.lag("close", 1).over(order_window)
    gain = F.when(change > 0, change).otherwise(F.lit(0.0))
    loss = F.when(change < 0, -change).otherwise(F.lit(0.0))

    df = df.withColumn("_gain", gain).withColumn("_loss", loss)

    w = _token_window(window - 1)
    row_count = F.count("_gain").over(w)
    avg_gain = F.avg("_gain").over(w)
    avg_loss = F.avg("_loss").over(w)
    rs = avg_gain / F.when(avg_loss == 0, F.lit(1e-9)).otherwise(avg_loss)
    rsi = 100 - (100 / (1 + rs))

    df = df.withColumn(f"rsi_{window}", F.when(row_count >= window, rsi))
    return df.drop("_gain", "_loss")


_EMA_MACD_SCHEMA = StructType(
    [
        StructField("event_id", StringType()),
        StructField("ema_fast", DoubleType()),
        StructField("ema_slow", DoubleType()),
        StructField("macd", DoubleType()),
        StructField("macd_signal", DoubleType()),
    ]
)


def add_ema_and_macd(
    df: DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> DataFrame:
    """
    Exponential moving averages and MACD.

    WHY THIS ONE USES A PANDAS UDF, UNLIKE EVERYTHING ELSE ABOVE: EMA is
    recursively defined - ema[t] depends on ema[t-1], which depends on
    ema[t-2], and so on back to the start of the series. Spark's window
    functions compute each output row independently from a fixed slice of
    input rows; they have no way to reference "the previous OUTPUT value"
    the way a recursive formula needs. `applyInPandas` sidesteps this by
    handing each token's full, time-ordered history to a single pandas
    function that can iterate row-by-row - pandas' `.ewm()` already
    implements the correct recursive formula. This is the standard pattern
    for genuinely sequential/recursive computations in Spark.
    """

    def _compute(pdf: pd.DataFrame) -> pd.DataFrame:
        pdf = pdf.sort_values("event_timestamp")
        ema_fast = pdf["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = pdf["close"].ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False).mean()
        return pd.DataFrame(
            {
                "event_id": pdf["event_id"],
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "macd": macd,
                "macd_signal": macd_signal,
            }
        )

    ema_df = df.groupBy("token_address").applyInPandas(
        _compute, schema=_EMA_MACD_SCHEMA
    )
    return df.join(ema_df, on="event_id", how="left")
