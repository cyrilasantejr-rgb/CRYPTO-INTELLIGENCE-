"""
Object storage abstraction over boto3's S3 client.

Why this exists: per ADR-007, local development targets MinIO (S3-API-compatible,
free, runs in Docker) while production targets real AWS S3. boto3's S3 client
already speaks both - the only difference is the `endpoint_url` passed at
construction time. Wrapping that construction here means every caller in the
codebase (ingestion adapters, Airflow tasks, etc.) writes code once and never
needs to know or care which backend it's actually talking to.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


class ObjectStoreClient:
    """Thin wrapper around boto3 S3 client with Parquet-aware helpers."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        region_name: str = "us-east-1",
    ):
        """
        endpoint_url: set this to point at MinIO (e.g. http://localhost:9000)
        for local dev. Leave as None in production to use real AWS S3's
        default endpoint resolution.
        """
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )

    def write_parquet(self, records: list[dict[str, Any]], key: str) -> str:
        """
        Serialize a list of dict records to Parquet and upload to `key`.

        Returns the full s3://bucket/key URI written.

        Note: this always PUTs to `key` - if `key` already exists, its
        content is replaced. Callers are responsible for choosing a key
        scheme that matches their durability requirements (see
        common/storage/bronze_writer.py for why we use run-scoped keys
        to keep the Bronze layer append-only).
        """
        if not records:
            raise ValueError("write_parquet called with an empty records list")

        table = pa.Table.from_pylist(records)
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        buffer.seek(0)

        self._client.put_object(Bucket=self.bucket, Key=key, Body=buffer.getvalue())
        uri = f"s3://{self.bucket}/{key}"
        logger.info("Wrote %d records to %s", len(records), uri)
        return uri

    def get_object_bytes(self, key: str) -> bytes:
        """Download an object's raw bytes. Used to stage Bronze files locally
        before a local Spark session reads them (see databricks/silver/
        market_silver.py for why local staging is used instead of s3a://)."""
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def put_object_bytes(self, key: str, data: bytes) -> str:
        """Upload raw bytes to a key. Used when a file was already written
        to local disk in the right format (e.g. Spark's own partitioned
        Parquet output) and just needs to land in object storage as-is,
        as opposed to write_parquet() which serializes Python dicts."""
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        """List all object keys under a prefix (paginated automatically)."""
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't already exist. Idempotent."""
        existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self._client.create_bucket(Bucket=self.bucket)
            logger.info("Created bucket %s", self.bucket)
