import boto3
import pytest
from moto import mock_aws

from common.storage.object_store import ObjectStoreClient


@pytest.fixture
def store():
    """
    moto's mock_aws intercepts all boto3 calls at the botocore layer and
    simulates AWS in-memory - no real network call is ever made, no AWS
    credentials are needed, and nothing costs money. This is what lets us
    unit-test S3 interaction logic in CI (and in this sandbox, which has no
    route to AWS at all).
    """
    with mock_aws():
        client = ObjectStoreClient(bucket="test-bucket", region_name="us-east-1")
        client.ensure_bucket()
        yield client


def test_ensure_bucket_is_idempotent(store):
    # Calling it again must not raise, even though the bucket already exists.
    store.ensure_bucket()
    store.ensure_bucket()


def test_write_parquet_round_trips_records(store):
    records = [
        {"event_id": "abc123", "token_address": "TokenA", "payload": {"c": 1.1}},
        {"event_id": "def456", "token_address": "TokenA", "payload": {"c": 1.2}},
    ]
    uri = store.write_parquet(records, key="bronze/market/dt=2026-08-01/test.parquet")

    assert uri == "s3://test-bucket/bronze/market/dt=2026-08-01/test.parquet"

    import io

    import pyarrow.parquet as pq

    raw = (
        boto3.client("s3", region_name="us-east-1")
        .get_object(Bucket="test-bucket", Key="bronze/market/dt=2026-08-01/test.parquet")[
            "Body"
        ]
        .read()
    )
    table = pq.read_table(io.BytesIO(raw))
    assert table.num_rows == 2
    assert set(table.column_names) == {"event_id", "token_address", "payload"}


def test_write_parquet_rejects_empty_records(store):
    with pytest.raises(ValueError):
        store.write_parquet([], key="bronze/market/dt=2026-08-01/empty.parquet")


def test_list_keys_returns_only_matching_prefix(store):
    store.write_parquet([{"a": 1}], key="bronze/market/dt=2026-08-01/x.parquet")
    store.write_parquet([{"a": 1}], key="bronze/market/dt=2026-08-02/y.parquet")
    store.write_parquet([{"a": 1}], key="bronze/social/dt=2026-08-01/z.parquet")

    market_keys = store.list_keys(prefix="bronze/market/")
    assert len(market_keys) == 2
    assert all(k.startswith("bronze/market/") for k in market_keys)


def test_read_parquet_round_trips_records(store):
    records = [
        {"event_id": "abc123", "token_address": "TokenA", "payload": {"c": 1.1}},
        {"event_id": "def456", "token_address": "TokenA", "payload": {"c": 1.2}},
    ]
    store.write_parquet(records, key="bronze/market/dt=2026-08-01/roundtrip.parquet")

    read_back = store.read_parquet(key="bronze/market/dt=2026-08-01/roundtrip.parquet")

    assert read_back == records
