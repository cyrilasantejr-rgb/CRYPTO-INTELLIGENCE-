"""
CryptoPanic implementation for fetching recent crypto news, filtered by
currency (e.g. SOL). CryptoPanic's community vote system (positive/
negative/important/liked/disliked/lol/toxic counts per post) is used as
the raw sentiment signal - see news_classification.py for why this is
kept SEPARATE from source credibility, never blended into one score.

API reference: https://cryptopanic.com/developers/api/about

HONEST CAVEAT, same as several adapters tonight: while the base URL
below is now confirmed against a real, working community integration
example (not just guessed from ambiguous docs - see ADR-030), the exact
response field names are still based on generally observed API shape,
not a live-verified response from this exact adapter.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://cryptopanic.com/api/v1/posts/"


class CryptoPanicNewsAdapter:
    source_name = "cryptopanic"

    def __init__(self, auth_token: str, max_retries: int = 3):
        self.auth_token = auth_token
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get_with_backoff(self, params: dict) -> dict:
        """Same retry/backoff pattern used throughout this project's
        adapters - see birdeye_adapter.py for the full reasoning."""
        for attempt in range(self.max_retries):
            response = self._session.get(BASE_URL, params=params, timeout=15)

            if response.status_code in (401, 403):
                raise PermissionError(
                    f"CryptoPanic auth failed ({response.status_code}) - check "
                    "auth_token or plan slug. Not retrying."
                )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                delay = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "CryptoPanic request failed (status=%s), attempt %d/%d, "
                    "retrying in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()

        raise RuntimeError(
            f"CryptoPanic request failed after {self.max_retries} retries"
        )

    def fetch_recent_news(self, currency: str, limit: int = 20) -> list[BronzeEnvelope]:
        """
        Returns one BronzeEnvelope per news post, each wrapping that
        post's full raw payload unparsed - same "store the raw payload,
        parse defensively downstream" pattern as the Phase 10 security
        adapter, given the field-name uncertainty noted above.
        """
        params = {
            "auth_token": self.auth_token,
            "currencies": currency,
            "public": "true",
        }
        response = self._get_with_backoff(params)
        results = response.get("results", [])[:limit]

        envelopes = []
        for post in results:
            published_at_raw = post.get("published_at") or post.get("created_at")
            event_timestamp = datetime.now(timezone.utc)
            if published_at_raw:
                try:
                    event_timestamp = datetime.fromisoformat(
                        published_at_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    logger.warning(
                        "Could not parse published_at timestamp: %s", published_at_raw
                    )

            post_id = post.get("id")
            envelopes.append(
                BronzeEnvelope.build(
                    source=self.source_name,
                    token_address=currency,  # used as topic key, not a mint address
                    event_timestamp=event_timestamp,
                    domain="news",
                    payload={**post, "_post_id": post_id},
                )
            )

        return envelopes
