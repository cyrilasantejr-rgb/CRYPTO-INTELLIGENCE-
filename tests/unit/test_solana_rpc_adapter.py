from unittest.mock import MagicMock, patch

import pytest

from rug_pull_intelligence.solana_rpc_adapter import SolanaMintInfoAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_mint_info_sends_correct_jsonrpc_payload():
    ok_response = make_response(
        200, {"result": {"value": {"data": {"parsed": {"info": {}}}}}}
    )
    adapter = SolanaMintInfoAdapter(helius_api_key="fake-key")

    with patch.object(adapter._session, "post", return_value=ok_response) as mock_post:
        envelope = adapter.fetch_mint_info("TokenMintAddress")

    assert mock_post.called
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["method"] == "getAccountInfo"
    assert sent_payload["params"][0] == "TokenMintAddress"
    assert sent_payload["params"][1]["encoding"] == "jsonParsed"

    assert envelope.domain == "security"
    assert envelope.token_address == "TokenMintAddress"
    assert envelope.source == "solana_rpc"


def test_jsonrpc_error_in_200_response_does_not_raise():
    """JSON-RPC errors come back as HTTP 200 with an 'error' field in
    the body, not an HTTP error status - must not be treated as a
    network failure, just returned as-is for the caller to interpret."""
    error_response = make_response(
        200, {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}}
    )
    adapter = SolanaMintInfoAdapter(helius_api_key="fake-key")

    with patch.object(adapter._session, "post", return_value=error_response):
        envelope = adapter.fetch_mint_info("BadAddress")

    assert "error" in envelope.payload


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = SolanaMintInfoAdapter(helius_api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "post", return_value=unauthorized) as mock_post,
        pytest.raises(PermissionError),
    ):
        adapter.fetch_mint_info("TokenA")

    assert mock_post.call_count == 1


def test_retries_on_rate_limit_then_succeeds():
    rate_limited = make_response(429, {})
    ok_response = make_response(200, {"result": {}})
    adapter = SolanaMintInfoAdapter(helius_api_key="fake-key", max_retries=3)

    with (
        patch.object(
            adapter._session, "post", side_effect=[rate_limited, ok_response]
        ) as mock_post,
        patch("rug_pull_intelligence.solana_rpc_adapter.time.sleep"),
    ):
        adapter.fetch_mint_info("TokenA")

    assert mock_post.call_count == 2
