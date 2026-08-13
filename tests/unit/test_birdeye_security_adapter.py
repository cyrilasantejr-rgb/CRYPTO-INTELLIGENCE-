from unittest.mock import MagicMock, patch

import pytest

from rug_pull_intelligence.birdeye_security_adapter import BirdeyeSecurityAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_security_info_returns_envelope_with_security_domain():
    ok_response = make_response(200, {"data": {"someField": True}})
    adapter = BirdeyeSecurityAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelope = adapter.fetch_security_info("TokenA")

    assert mock_get.called
    assert envelope.domain == "security"
    assert envelope.token_address == "TokenA"
    assert envelope.source == "birdeye"
    assert envelope.payload == {"someField": True}


def test_missing_data_key_returns_empty_payload_not_crash():
    ok_response = make_response(200, {"success": True})  # no "data" key at all
    adapter = BirdeyeSecurityAdapter(api_key="fake-key")

    with patch.object(adapter._session, "get", return_value=ok_response):
        envelope = adapter.fetch_security_info("TokenA")

    assert envelope.payload == {}


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = BirdeyeSecurityAdapter(api_key="bad-key", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        adapter.fetch_security_info("TokenA")

    assert mock_get.call_count == 1
