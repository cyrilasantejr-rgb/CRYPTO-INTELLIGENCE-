"""
Runner for Phase 4: Silver -> Gold feature engineering for market data.

Usage (run locally, with MinIO running via `docker compose up -d minio`):

    python3 -m features.run_market_features

Downloads Silver market Parquet from MinIO, computes the full
token_market_features table (returns, SMA, volatility, RSI, EMA, MACD),
and uploads the result to gold/token_market_features/, partitioned by
token_address and event_date.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from common.storage.object_store import ObjectStoreClient
from databricks.silver.market_silver import get_or_create_spark
from features.market import build_token_market_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SILVER_PREFIX = "silver/market/"
GOLD_PREFIX = "gold/token_market_features"


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


def _download_locally(store: ObjectStoreClient, prefix: str, local_dir: Path) -> int:
    keys = store.list_keys(prefix=prefix)
    if not keys:
        logger.warning("No objects found under %s", prefix)
        return 0
    for i, key in enumerate(keys):
        data = store.get_object_bytes(key)
        (local_dir / f"file_{i}.parquet").write_bytes(data)
    return len(keys)


def _upload_partitioned(store: ObjectStoreClient, local_dir: Path, prefix: str) -> int:
    count = 0
    for path in local_dir.rglob("*.parquet"):
        relative = path.relative_to(local_dir)
        key = f"{prefix}/{relative.as_posix()}"
        store.put_object_bytes(key, path.read_bytes())
        count += 1
    return count


def run() -> None:
    store = _make_store()
    spark = get_or_create_spark(app_name="market-features")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        silver_dir = tmp_path / "silver"
        gold_dir = tmp_path / "gold"
        silver_dir.mkdir()

        n_downloaded = _download_locally(store, SILVER_PREFIX, silver_dir)
        if n_downloaded == 0:
            spark.stop()
            return
        logger.info("Downloaded %d Silver file(s) locally", n_downloaded)

        silver_df = spark.read.parquet(str(silver_dir))
        gold_df = build_token_market_features(silver_df)

        row_count = gold_df.count()
        logger.info("Computed features for %d row(s)", row_count)

        gold_df.write.partitionBy("token_address", "event_date").mode(
            "overwrite"
        ).parquet(str(gold_dir))
        n_uploaded = _upload_partitioned(store, gold_dir, GOLD_PREFIX)
        logger.info("Uploaded %d Gold partition file(s) to MinIO", n_uploaded)

    spark.stop()


if __name__ == "__main__":
    run()
