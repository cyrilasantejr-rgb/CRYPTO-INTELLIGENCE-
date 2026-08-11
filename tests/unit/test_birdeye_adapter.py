from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ingestion.market.birdeye_adapter import BirdeyeAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_converts_candles_to_envelopes():
    candle = {"unixTime": 1754000000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 500}
    ok_response = make_response(200, {"data": {"items": [candle]}})

    adapter = BirdeyeAdapter(api_key="fake-key")
    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelopes = list(
            adapter.fetch_historical_ohlcv(
                token_address="TokenA",
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
                interval="1H",
            )
        )

    assert mock_get.called
    assert len(envelopes) == 1
    assert envelopes[0].source == "birdeye"
    assert envelopes[0].token_address == "TokenA"
    assert envelopes[0].payload["c"] == 1.05


def test_malformed_candle_is_skipped_not_fatal():
    good = {"unixTime": 1754000000, "c": 1.0}
    bad = {"c": 1.0}  # missing unixTime - should be skipped, not crash the run
    ok_response = make_response(200, {"data": {"items": [good, bad]}})

    adapter = BirdeyeAdapter(api_key="fake-key")
    with patch.object(adapter._session, "get", return_value=ok_response):
        envelopes = list(
            adapter.fetch_historical_ohlcv(
                token_address="TokenA",
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
                interval="1H",
            )
        )

    assert len(envelopes) == 1  # only the good candle survives


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = BirdeyeAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        list(
            adapter.fetch_historical_ohlcv(
                token_address="TokenA",
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
                interval="1H",
            )
        )

    # Must fail on the FIRST attempt - retrying a bad API key wastes calls
    # and never succeeds.
    assert mock_get.call_count == 1


def test_retries_on_rate_limit_then_succeeds():
    rate_limited = make_response(429, {})
    ok_response = make_response(200, {"data": {"items": []}})
    adapter = BirdeyeAdapter(api_key="fake-key", max_retries=3)

    with (
        patch.object(
            adapter._session, "get", side_effect=[rate_limited, ok_response]
        ) as mock_get,
        patch("ingestion.market.birdeye_adapter.time.sleep"),
    ):
        list(
            adapter.fetch_historical_ohlcv(
                token_address="TokenA",
                start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
                interval="1H",
            )
        )

    assert mock_get.call_count == 2  # first 429, then success


def test_long_date_range_is_chunked_into_multiple_requests():
    """1H interval, 900 records/chunk = 900 hours per chunk (~37.5 days).
    A 100-day range should require 3 requests, not 1."""
    ok_response = make_response(200, {"data": {"items": []}})
    adapter = BirdeyeAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        list(
            adapter.fetch_historical_ohlcv(
                token_address="TokenA",
                start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end=datetime(2026, 4, 11, tzinfo=timezone.utc),  # ~100 days
                interval="1H",
            )
        )

    assert mock_get.call_count == 3
