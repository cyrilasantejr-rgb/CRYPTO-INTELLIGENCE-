from datetime import datetime, timezone

import pytest
from moto import mock_aws

from common.schemas.envelope import BronzeEnvelope
from common.storage.bronze_writer import write_bronze_batch
from common.storage.object_store import ObjectStoreClient


@pytest.fixture
def store():
    with mock_aws():
        client = ObjectStoreClient(bucket="test-bucket", region_name="us-east-1")
        client.ensure_bucket()
        yield client


def make_envelope(token_address: str, event_date: str, source: str = "birdeye"):
    return BronzeEnvelope.build(
        source=source,
        token_address=token_address,
        event_timestamp=datetime.fromisoformat(event_date).replace(tzinfo=timezone.utc),
        domain="market",
        payload={"c": 1.0},
    )


def test_empty_batch_writes_nothing(store):
    assert write_bronze_batch([], store) == []


def test_partitions_by_domain_date_and_token(store):
    envelopes = [
        make_envelope("TokenA", "2026-08-01"),
        make_envelope("TokenA", "2026-08-01"),  # same partition
        make_envelope("TokenA", "2026-08-02"),  # different date
        make_envelope("TokenB", "2026-08-01"),  # different token
    ]

    uris = write_bronze_batch(envelopes, store)

    # 3 distinct (domain, date, token) groups -> 3 files, even though the
    # first two envelopes share a partition and get combined into one file.
    assert len(uris) == 3
    assert any("dt=2026-08-01/token=TokenA/" in uri for uri in uris)
    assert any("dt=2026-08-02/token=TokenA/" in uri for uri in uris)
    assert any("dt=2026-08-01/token=TokenB/" in uri for uri in uris)


def test_repeated_runs_do_not_overwrite_each_other(store):
    """This is the core Bronze-layer guarantee: append-only. Two separate
    ingestion runs covering the same partition must both survive as
    separate files, not have the second overwrite the first."""
    first_run = [make_envelope("TokenA", "2026-08-01")]
    second_run = [make_envelope("TokenA", "2026-08-01")]

    uris_1 = write_bronze_batch(first_run, store)
    uris_2 = write_bronze_batch(second_run, store)

    assert uris_1[0] != uris_2[0]  # different run_id in the key -> different file

    all_keys = store.list_keys(prefix="bronze/market/dt=2026-08-01/token=TokenA/")
    assert len(all_keys) == 2
