from unittest.mock import MagicMock, patch

import pytest

from news_intelligence.cryptopanic_adapter import CryptoPanicNewsAdapter


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def test_fetch_recent_news_returns_one_envelope_per_post():
    ok_response = make_response(
        200,
        {
            "results": [
                {
                    "id": 1,
                    "title": "Solana partners with major firm",
                    "published_at": "2026-08-13T10:00:00Z",
                    "domain": "coindesk.com",
                },
                {
                    "id": 2,
                    "title": "SOL price rallies",
                    "published_at": "2026-08-13T11:00:00Z",
                    "domain": "some-blog.xyz",
                },
            ]
        },
    )
    adapter = CryptoPanicNewsAdapter(auth_token="fake-token")

    with patch.object(adapter._session, "get", return_value=ok_response) as mock_get:
        envelopes = adapter.fetch_recent_news("SOL")

    assert mock_get.called
    call_params = mock_get.call_args.kwargs["params"]
    assert call_params["currencies"] == "SOL"
    assert call_params["auth_token"] == "fake-token"

    assert len(envelopes) == 2
    assert envelopes[0].domain == "news"
    assert envelopes[0].source == "cryptopanic"
    assert envelopes[0].payload["title"] == "Solana partners with major firm"


def test_limit_truncates_results_client_side():
    many_posts = [{"id": i, "title": f"Post {i}"} for i in range(10)]
    ok_response = make_response(200, {"results": many_posts})
    adapter = CryptoPanicNewsAdapter(auth_token="fake-token")

    with patch.object(adapter._session, "get", return_value=ok_response):
        envelopes = adapter.fetch_recent_news("SOL", limit=3)

    assert len(envelopes) == 3


def test_missing_published_at_falls_back_to_now_not_crash():
    ok_response = make_response(
        200, {"results": [{"id": 1, "title": "No timestamp post"}]}
    )
    adapter = CryptoPanicNewsAdapter(auth_token="fake-token")

    with patch.object(adapter._session, "get", return_value=ok_response):
        envelopes = adapter.fetch_recent_news("SOL")

    assert (
        len(envelopes) == 1
    )  # still produced an envelope, just with a fallback timestamp


def test_empty_results_returns_empty_list():
    ok_response = make_response(200, {"results": []})
    adapter = CryptoPanicNewsAdapter(auth_token="fake-token")

    with patch.object(adapter._session, "get", return_value=ok_response):
        envelopes = adapter.fetch_recent_news("SOL")

    assert envelopes == []


def test_auth_error_raises_immediately_without_retry():
    unauthorized = make_response(401, {})
    adapter = CryptoPanicNewsAdapter(auth_token="bad-token", max_retries=5)

    with (
        patch.object(adapter._session, "get", return_value=unauthorized) as mock_get,
        pytest.raises(PermissionError),
    ):
        adapter.fetch_recent_news("SOL")

    assert mock_get.call_count == 1
