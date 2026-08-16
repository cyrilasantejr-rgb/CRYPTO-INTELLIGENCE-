"""
Unit tests for discovery/bronze_reader.py - the read-side counterpart to
run_candidate_fetch.py's Bronze writes. Same moto-based mocking pattern
as test_object_store.py and test_bronze_writer.py - no real network call.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from moto import mock_aws

from common.schemas.envelope import BronzeEnvelope
from common.storage.bronze_writer import write_bronze_batch
from common.storage.object_store import ObjectStoreClient
from discovery.bronze_reader import _latest_partition_date, read_latest_valid_candidates


def _make_candidate(address: str, liquidity: float, volume_24h: float) -> BronzeEnvelope:
    payload = {"address": address, "liquidity": liquidity, "volume_24h_usd": volume_24h}
    return BronzeEnvelope.build(
        source="test",
        token_address=address,
        event_timestamp=datetime.now(timezone.utc),
        domain="discovery",
        payload=payload,
    )


@pytest.fixture
def store():
    with mock_aws():
        client = ObjectStoreClient(bucket="test-bucket", region_name="us-east-1")
        client.ensure_bucket()
        yield client


def test_latest_partition_date_returns_none_when_empty(store):
    assert _latest_partition_date(store) is None


def test_latest_partition_date_picks_most_recent(store):
    good = _make_candidate("addr1", liquidity=20_000, volume_24h=50_000)
    write_bronze_batch([good], store)  # writes under today's real dt=

    old_record = good.model_dump(mode="json")
    store.write_parquet(
        [old_record], key="bronze/discovery/dt=2020-01-01/token=old/manual.parquet"
    )

    latest = _latest_partition_date(store)
    assert latest is not None
    assert latest > "2020-01-01"


def test_read_latest_valid_candidates_filters_quarantined(store):
    good = _make_candidate("addr_good", liquidity=20_000, volume_24h=50_000)
    bad = _make_candidate("addr_bad", liquidity=12_884, volume_24h=81_413_719)
    write_bronze_batch([good, bad], store)

    valid = read_latest_valid_candidates(store)

    valid_addresses = {envelope.token_address for envelope in valid}
    assert valid_addresses == {"addr_good"}


def test_read_latest_valid_candidates_returns_empty_when_no_data(store):
    assert read_latest_valid_candidates(store) == []
