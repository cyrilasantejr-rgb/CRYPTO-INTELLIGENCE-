"""
Tests for flatten_and_validate(). These use a local SparkSession with a
handful of in-memory rows - no MinIO, no real Bronze files, no network.
This is the payoff of separating pure transform logic from I/O: these
tests run in a couple seconds instead of minutes, and never depend on
external infrastructure being up.
"""

from datetime import datetime, timezone

import pytest
from pyspark.sql import Row

from databricks.silver.market_silver import flatten_and_validate, get_or_create_spark


@pytest.fixture(scope="module")
def spark():
    session = get_or_create_spark(app_name="test-market-silver")
    yield session
    session.stop()


def make_bronze_row(
    event_id: str,
    token_address: str = "TokenA",
    o: float = 1.0,
    h: float = 1.1,
    l: float = 0.9,
    c: float = 1.05,
    v: float = 100.0,
):
    return Row(
        event_id=event_id,
        source="birdeye",
        schema_version="1.0",
        ingestion_timestamp=datetime(2026, 8, 11, tzinfo=timezone.utc),
        event_timestamp=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        token_address=token_address,
        domain="market",
        payload=Row(o=o, h=h, l=l, c=c, v=v, vUsd=v * c, currency="usd"),
    )


def test_valid_rows_pass_through(spark):
    df = spark.createDataFrame([make_bronze_row("evt1"), make_bronze_row("evt2")])
    valid_df, quarantine_df = flatten_and_validate(df)

    assert valid_df.count() == 2
    assert quarantine_df.count() == 0
    row = valid_df.filter("event_id = 'evt1'").first()
    assert row.open == 1.0
    assert row.close == 1.05
    assert row.event_date is not None


def test_duplicate_event_id_collapses_to_one_row(spark):
    """This is the core Silver-layer guarantee: Bronze can (and does, per
    ADR-008) contain the same event_id across multiple files. Silver is
    where that gets collapsed to exactly one row."""
    df = spark.createDataFrame([make_bronze_row("evt1"), make_bronze_row("evt1")])
    valid_df, _ = flatten_and_validate(df)

    assert valid_df.count() == 1


def test_negative_price_is_quarantined(spark):
    df = spark.createDataFrame([make_bronze_row("evt1", c=-5.0)])
    valid_df, quarantine_df = flatten_and_validate(df)

    assert valid_df.count() == 0
    assert quarantine_df.count() == 1
    assert quarantine_df.first().close == -5.0


def test_zero_price_is_quarantined(spark):
    df = spark.createDataFrame([make_bronze_row("evt1", o=0.0)])
    valid_df, quarantine_df = flatten_and_validate(df)

    assert valid_df.count() == 0
    assert quarantine_df.count() == 1


def test_mixed_batch_splits_correctly(spark):
    df = spark.createDataFrame(
        [
            make_bronze_row("good1"),
            make_bronze_row("bad1", c=-1.0),
            make_bronze_row("good2"),
            make_bronze_row("bad2", h=0.0),
        ]
    )
    valid_df, quarantine_df = flatten_and_validate(df)

    assert valid_df.count() == 2
    assert quarantine_df.count() == 2
    valid_ids = {r.event_id for r in valid_df.collect()}
    assert valid_ids == {"good1", "good2"}
