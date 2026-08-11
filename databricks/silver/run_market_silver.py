"""
Runner for Phase 3: Bronze -> Silver for market data.

Usage (run locally, with MinIO running via `docker compose up -d minio`):

    python3 -m databricks.silver.run_market_silver

This downloads every Bronze market Parquet file from MinIO into a local temp
directory, runs them through flatten_and_validate() (see market_silver.py
for why Spark reads local disk here instead of s3a:// directly), then
uploads the cleaned Silver output and any quarantined rows back to MinIO
under silver/market/ and quarantine/market/ respectively.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from common.storage.object_store import ObjectStoreClient
from databricks.silver.market_silver import flatten_and_validate, get_or_create_spark

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BRONZE_PREFIX = "bronze/market/"
SILVER_PREFIX = "silver/market"
QUARANTINE_PREFIX = "quarantine/market"


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


def _download_bronze_locally(store: ObjectStoreClient, local_dir: Path) -> int:
    keys = store.list_keys(prefix=BRONZE_PREFIX)
    if not keys:
        logger.warning("No Bronze objects found under %s", BRONZE_PREFIX)
        return 0

    for i, key in enumerate(keys):
        data = store.get_object_bytes(key)
        # Flat filenames are fine here - we only need Spark to read every
        # file in the directory; the actual token/date/domain fields live
        # inside each row already (that's WHY the envelope carries them).
        (local_dir / f"file_{i}.parquet").write_bytes(data)

    return len(keys)


def _upload_partitioned(store: ObjectStoreClient, local_dir: Path, prefix: str) -> int:
    """
    Walks a Spark-written, partitionBy()'d local directory tree (which
    looks like token_address=X/event_date=Y/part-00000-....parquet) and
    uploads each actual data file to MinIO under the matching key.
    """
    count = 0
    for path in local_dir.rglob("*.parquet"):
        relative = path.relative_to(local_dir)
        key = f"{prefix}/{relative.as_posix()}"
        store.put_object_bytes(key, path.read_bytes())
        count += 1
    return count


def run() -> None:
    store = _make_store()
    spark = get_or_create_spark()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bronze_dir = tmp_path / "bronze"
        silver_dir = tmp_path / "silver"
        quarantine_dir = tmp_path / "quarantine"
        bronze_dir.mkdir()

        n_downloaded = _download_bronze_locally(store, bronze_dir)
        if n_downloaded == 0:
            spark.stop()
            return
        logger.info("Downloaded %d Bronze file(s) locally", n_downloaded)

        bronze_df = spark.read.parquet(str(bronze_dir))
        valid_df, quarantine_df = flatten_and_validate(bronze_df)

        valid_count = valid_df.count()
        quarantine_count = quarantine_df.count()
        logger.info(
            "Silver transform: %d valid row(s), %d quarantined row(s)",
            valid_count,
            quarantine_count,
        )

        if valid_count > 0:
            valid_df.write.partitionBy("token_address", "event_date").mode(
                "overwrite"
            ).parquet(str(silver_dir))
            n = _upload_partitioned(store, silver_dir, SILVER_PREFIX)
            logger.info("Uploaded %d Silver partition file(s) to MinIO", n)

        if quarantine_count > 0:
            quarantine_df.write.partitionBy("token_address", "event_date").mode(
                "overwrite"
            ).parquet(str(quarantine_dir))
            n = _upload_partitioned(store, quarantine_dir, QUARANTINE_PREFIX)
            logger.info("Uploaded %d quarantine partition file(s) to MinIO", n)

    spark.stop()


if __name__ == "__main__":
    run()
