"""
Bronze -> Silver transformation for market data.

This module is split deliberately into two halves:

  - `flatten_and_validate()`: pure transformation logic. Takes a Spark
    DataFrame, returns two Spark DataFrames (valid, quarantined). No file
    I/O, no network calls, no MinIO/S3 client. This is what unit tests
    exercise directly - a local SparkSession with a handful of in-memory
    rows runs in under a second, versus minutes if every test had to spin
    up MinIO and download real Bronze files.

  - `run()` in the accompanying script (run_market_silver.py): the
    orchestration layer. Downloads Bronze from MinIO, calls the pure
    function above, uploads the results. This is NOT unit tested directly -
    it's thin glue code, and testing it would mean testing MinIO/boto3,
    which we already tested independently in test_object_store.py.

Why this split matters: it's the same principle as ADR-... (see
common/schemas/envelope.py) - keep the part with actual logic testable in
isolation, and keep the part that talks to the outside world as thin and
boring as possible.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType


def flatten_and_validate(bronze_df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """
    Takes raw Bronze market events (envelope + nested `payload` struct) and
    returns (valid_silver_df, quarantine_df).

    Transformations applied:
      1. Deduplicate on event_id - our deterministic dedup key (ADR-004).
         Multiple Bronze files can contain the same event_id (that's the
         whole point of the append-only design in ADR-008), so Silver is
         where duplicates actually get collapsed to one row.
      2. Flatten payload.o/h/l/c/v into typed top-level columns instead of
         leaving them buried in a nested struct - much easier for anyone
         querying Silver later (features, backtesting, dashboards).
      3. Derive an `event_date` column from event_timestamp, used for
         Silver's own partitioning scheme.
      4. Split into valid vs quarantined rows based on data-quality rules:
         negative/zero prices, or a null token_address/event_timestamp are
         quarantined rather than silently dropped.
    """
    deduped = bronze_df.dropDuplicates(["event_id"])

    flattened = deduped.select(
        "event_id",
        "source",
        "schema_version",
        "ingestion_timestamp",
        "event_timestamp",
        "token_address",
        "domain",
        F.col("payload.o").cast(DoubleType()).alias("open"),
        F.col("payload.h").cast(DoubleType()).alias("high"),
        F.col("payload.l").cast(DoubleType()).alias("low"),
        F.col("payload.c").cast(DoubleType()).alias("close"),
        F.col("payload.v").cast(DoubleType()).alias("volume"),
        F.col("payload.vUsd").cast(DoubleType()).alias("volume_usd"),
        F.col("payload.currency").alias("currency"),
        F.to_date("event_timestamp").alias("event_date"),
    )

    is_valid = (
        F.col("token_address").isNotNull()
        & F.col("event_timestamp").isNotNull()
        & F.col("open").isNotNull()
        & (F.col("open") > 0)
        & (F.col("high") > 0)
        & (F.col("low") > 0)
        & (F.col("close") > 0)
        & (F.col("volume") >= 0)
    )

    valid_df = flattened.filter(is_valid)
    quarantine_df = flattened.filter(~is_valid)

    return valid_df, quarantine_df


def get_or_create_spark(app_name: str = "market-silver") -> SparkSession:
    """
    Local SparkSession. Running with .master("local[*]") means Spark uses
    all available cores on THIS machine as its "cluster" - no actual
    cluster involved. The exact same transformation code (flatten_and_
    validate above) runs unchanged on a real multi-node Databricks
    cluster; only this session-creation line would differ in production.
    """
    return (
        SparkSession.builder.appName(app_name)
        .master("local[*]")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
