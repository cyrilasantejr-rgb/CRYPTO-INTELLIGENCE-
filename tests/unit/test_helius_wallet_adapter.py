from unittest.mock import MagicMock, patch

import pytest

from wallet_intelligence.helius_wallet_adapter import HeliusWalletAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    return resp


def test_fetch_transactions_returns_envelope_with_wallet_domain():
    ok_response = make_response(200, [{"timestamp": 1754000000, "tokenTransfers": []}])
    adapter = HeliusWalletAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelope = adapter.fetch_transactions("WalletAddress123", limit=50)

    assert mock_get.called
    call_url = mock_get.call_args.args[0]
    assert "WalletAddress123/transactions" in call_url
    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["limit"] == 50

    assert envelope.domain == "wallet"
    assert envelope.token_address == "WalletAddress123"
    assert envelope.source == "helius"
    assert len(envelope.payload["transactions"]) == 1


def test_limit_is_capped_at_100():
    ok_response = make_response(200, [])
    adapter = HeliusWalletAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        adapter.fetch_transactions("WalletAddress123", limit=500)

    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["limit"] == 100


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = HeliusWalletAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        adapter.fetch_transactions("WalletAddress123")

    assert mock_get.call_count == 1


def test_retries_on_rate_limit_then_succeeds():
    rate_limited = make_response(429, {})
    ok_response = make_response(200, [])
    adapter = HeliusWalletAdapter(api_key="fake-key", max_retries=3)

    with (
        patch.object(
            adapter._session, "get", side_effect=[rate_limited, ok_response]
        ) as mock_get,
        patch("wallet_intelligence.helius_wallet_adapter.time.sleep"),
    ):
        adapter.fetch_transactions("WalletAddress123")

    assert mock_get.call_count == 2
