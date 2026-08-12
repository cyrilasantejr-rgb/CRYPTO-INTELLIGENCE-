from unittest.mock import MagicMock, patch

import pytest

from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_latest_prices_converts_to_envelopes():
    """One call PER token now (not batched) - Standard tier only has
    access to the single-token /defi/price endpoint. See ADR-021."""
    responses = [
        make_response(200, {"data": {"value": 1.23, "updateUnixTime": 1754000000}}),
        make_response(200, {"data": {"value": 4.56, "updateUnixTime": 1754000001}}),
    ]
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", side_effect=responses) as mock_get:
        envelopes = list(adapter.fetch_latest_prices(["TokenA", "TokenB"]))

    assert mock_get.call_count == 2  # one call per token, not one batched call
    first_call_params = mock_get.call_args_list[0].kwargs["params"]
    assert first_call_params["address"] == "TokenA"

    assert len(envelopes) == 2
    assert envelopes[0].token_address == "TokenA"
    assert envelopes[0].payload["value"] == 1.23
    assert envelopes[1].token_address == "TokenB"


def test_missing_token_is_skipped_not_fatal():
    responses = [
        make_response(200, {"data": {"value": 1.23, "updateUnixTime": 1754000000}}),
        make_response(200, {"data": None}),
    ]
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", side_effect=responses):
        envelopes = list(adapter.fetch_latest_prices(["TokenA", "TokenB"]))

    assert len(envelopes) == 1
    assert envelopes[0].token_address == "TokenA"


def test_empty_watchlist_makes_no_call():
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key")
    with patch.object(adapter._session, "get") as mock_get:
        envelopes = list(adapter.fetch_latest_prices([]))
    assert envelopes == []
    assert not mock_get.called


def test_one_bad_token_does_not_stop_the_rest():
    """If fetching TokenA fails with a transient error, TokenB should
    still be attempted - one bad token per poll shouldn't block the
    whole watchlist, same 'don't let one bad record kill the batch'
    principle used throughout this project."""
    responses = [
        make_response(500, {}),
        make_response(500, {}),
        make_response(500, {}),  # TokenA exhausts retries and fails
        make_response(200, {"data": {"value": 4.56, "updateUnixTime": 1754000001}}),
    ]
    adapter = BirdeyeRealtimePriceAdapter(api_key="fake-key", max_retries=3)

    with (
        patch.object(adapter._session, "get", side_effect=responses),
        patch("ingestion.market.birdeye_realtime_adapter.time.sleep"),
    ):
        envelopes = list(adapter.fetch_latest_prices(["TokenA", "TokenB"]))

    assert len(envelopes) == 1
    assert envelopes[0].token_address == "TokenB"


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = BirdeyeRealtimePriceAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        list(adapter.fetch_latest_prices(["TokenA"]))

    assert mock_get.call_count == 1
