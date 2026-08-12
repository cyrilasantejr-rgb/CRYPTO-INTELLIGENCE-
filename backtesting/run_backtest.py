"""
Runner for Phase 5: backtest the RSI placeholder strategy against Gold
features.

Usage (run locally, with MinIO running via `docker compose up -d minio`):

    python3 -m backtesting.run_backtest

Downloads gold/token_market_features/ from MinIO directly into a pandas
DataFrame (via pyarrow, not Spark - see ADR-011), runs the backtest
engine, prints a metrics summary, and uploads the full equity-curve
DataFrame back to MinIO under gold/backtest_results/.
"""

from __future__ import annotations

import io
import logging
import os

import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

from backtesting.engine import (
    compute_metrics,
    compute_positions,
    generate_rsi_signals,
    run_backtest,
)
from common.storage.object_store import ObjectStoreClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GOLD_PREFIX = "gold/token_market_features/"
RESULTS_PREFIX = "gold/backtest_results"


def _make_store() -> ObjectStoreClient:
    load_dotenv()
    return ObjectStoreClient(
        bucket=os.environ.get("S3_BUCKET_NAME", "crypto-intelligence"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_SECRET_KEY"),
    )


def _load_gold_as_pandas(store: ObjectStoreClient) -> pd.DataFrame:
    """
    Reads every Gold Parquet file into one pandas DataFrame via pyarrow -
    no Spark session needed for this phase (ADR-011).

    Gold was written by Spark's partitionBy("token_address", "event_date"),
    same as Silver - meaning those two columns live only in the folder
    names (Hive-style partitioning, see ADR-010), not inside the file
    content. Since we're reading with plain pyarrow here instead of
    Spark, there's no automatic partition discovery, so we parse the
    partition values back out of each object's key manually and add
    them as columns after reading.
    """
    keys = store.list_keys(prefix=GOLD_PREFIX)
    if not keys:
        raise RuntimeError(f"No Gold objects found under {GOLD_PREFIX}")

    frames = []
    for key in keys:
        relative = key.removeprefix(GOLD_PREFIX)
        # Partition values are folder names in the key itself, e.g.
        # "token_address=X/event_date=Y/part-...parquet" - parse them
        # back out manually since we're not using Spark's partition
        # discovery here.
        parts = dict(
            segment.split("=", 1)
            for segment in relative.split("/")[:-1]
            if "=" in segment
        )

        table = pq.read_table(io.BytesIO(store.get_object_bytes(key)))
        chunk = table.to_pandas()
        for col, value in parts.items():
            chunk[col] = value
        frames.append(chunk)

    return pd.concat(frames, ignore_index=True)


def run(
    oversold: float = 30.0, overbought: float = 70.0, fee_bps: float = 10.0
) -> None:
    store = _make_store()

    gold_df = _load_gold_as_pandas(store)
    logger.info("Loaded %d Gold row(s)", len(gold_df))

    results = []
    for token, group in gold_df.groupby("token_address"):
        group = group.sort_values("event_timestamp")
        group = generate_rsi_signals(group, oversold=oversold, overbought=overbought)
        group = compute_positions(group)
        group = run_backtest(group, fee_bps=fee_bps)
        metrics = compute_metrics(group)

        logger.info("--- %s ---", token)
        for key, value in metrics.items():
            logger.info("  %s: %s", key, value)

        results.append(group)

    combined = pd.concat(results, ignore_index=True)

    buffer = io.BytesIO()
    combined.to_parquet(buffer, index=False)
    key = f"{RESULTS_PREFIX}/rsi_strategy_results.parquet"
    store.put_object_bytes(key, buffer.getvalue())
    logger.info("Uploaded backtest results to s3://%s/%s", store.bucket, key)


if __name__ == "__main__":
    run()
