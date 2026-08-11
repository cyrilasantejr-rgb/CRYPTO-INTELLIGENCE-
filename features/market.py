"""
Assembles the individual feature functions in market_features.py into the
final token_market_features Gold table.
"""

from __future__ import annotations

from pyspark.sql import DataFrame

from features.market_features import (
    add_ema_and_macd,
    add_returns,
    add_rsi,
    add_sma,
    add_volatility,
)


def build_token_market_features(silver_df: DataFrame) -> DataFrame:
    """
    Takes clean Silver market rows (one row per candle: event_id,
    token_address, event_timestamp, open/high/low/close/volume, ...) and
    returns the Gold token_market_features table.

    Order matters here: add_volatility() depends on return_1h already
    existing, so add_returns() must run first. This kind of dependency
    is exactly why this assembly step is a separate function from the
    individual feature functions - the order is a real design decision,
    not an implementation detail to bury.
    """
    df = add_returns(silver_df, periods=[1, 4, 24])
    df = add_sma(df, window=20)
    df = add_volatility(df, window=20)
    df = add_rsi(df, window=14)
    df = add_ema_and_macd(df, fast=12, slow=26, signal=9)
    return df
