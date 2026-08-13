from unittest.mock import MagicMock, patch

import pytest

from wallet_intelligence.birdeye_holder_adapter import BirdeyeHolderAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_top_holders_returns_envelope_with_holder_domain():
    ok_response = make_response(
        200,
        {
            "data": {
                "items": [
                    {"owner": "WalletA", "amount": 1000.0},
                    {"owner": "WalletB", "amount": 500.0},
                ],
                "holder": 2,
                "top10_hold_percent": 100.0,
            }
        },
    )
    adapter = BirdeyeHolderAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelope = adapter.fetch_top_holders("TokenA", limit=50)

    assert mock_get.called
    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["address"] == "TokenA"
    assert call_params["mode"] == "wallet"
    assert call_params["limit"] == 50

    assert envelope.domain == "holder"
    assert envelope.token_address == "TokenA"
    assert envelope.source == "birdeye"
    assert len(envelope.payload["items"]) == 2


def test_limit_is_capped_at_vendor_maximum():
    ok_response = make_response(200, {"data": {"items": []}})
    adapter = BirdeyeHolderAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        adapter.fetch_top_holders("TokenA", limit=500)

    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["limit"] == 100  # vendor caps at 100, not the requested 500


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = BirdeyeHolderAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        adapter.fetch_top_holders("TokenA")

    assert mock_get.call_count == 1


def test_retries_on_rate_limit_then_succeeds():
    rate_limited = make_response(429, {})
    ok_response = make_response(200, {"data": {"items": []}})
    adapter = BirdeyeHolderAdapter(api_key="fake-key", max_retries=3)

    with (
        patch.object(
            adapter._session, "get", side_effect=[rate_limited, ok_response]
        ) as mock_get,
        patch("wallet_intelligence.birdeye_holder_adapter.time.sleep"),
    ):
        adapter.fetch_top_holders("TokenA")

    assert mock_get.call_count == 2
