from unittest.mock import MagicMock, patch

import pytest

from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_latest_prices_converts_to_envelopes():
    ok_response = make_response(
        200,
        {
            "data": {
                "TokenA": {"value": 1.23, "updateUnixTime": 1754000000},
                "TokenB": {"value": 4.56, "updateUnixTime": 1754000001},
            }
        },
    )
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelopes = list(adapter.fetch_latest_prices(["TokenA", "TokenB"]))

    assert mock_get.called
    # single batched call, not one per token
    assert mock_get.call_count == 1
    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["list_address"] == "TokenA,TokenB"

    assert len(envelopes) == 2
    assert envelopes[0].token_address == "TokenA"
    assert envelopes[0].payload["value"] == 1.23


def test_missing_token_is_skipped_not_fatal():
    """Birdeye's docs explicitly warn that unknown/unsupported tokens
    return null - one missing token must not break the rest of the poll."""
    ok_response = make_response(
        200,
        {
            "data": {
                "TokenA": {"value": 1.23, "updateUnixTime": 1754000000},
                "TokenB": None,
            }
        },
    )
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response):
        envelopes = list(adapter.fetch_latest_prices(["TokenA", "TokenB"]))

    assert len(envelopes) == 1
    assert envelopes[0].token_address == "TokenA"


def test_empty_watchlist_makes_no_call():
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")
    with patch.object(adapter._session, "get") as mock_get:
        envelopes = list(adapter.fetch_latest_prices([]))
    assert envelopes == []
    assert not mock_get.called


def test_too_many_addresses_raises_before_any_call():
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")
    too_many = [f"Token{i}" for i in range(101)]
    with (
        patch.object(adapter._session, "get") as mock_get,
        pytest.raises(ValueError, match="caps at 100"),
    ):
        list(adapter.fetch_latest_prices(too_many))
    assert not mock_get.called


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = BirdeyeRealtimePriceAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        list(adapter.fetch_latest_prices(["TokenA"]))

    assert mock_get.call_count == 1
