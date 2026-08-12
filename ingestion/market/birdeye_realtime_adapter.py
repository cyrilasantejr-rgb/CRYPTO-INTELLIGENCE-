"""
Birdeye implementation of RealtimePriceAdapter, using the /defi/multi_price
endpoint to poll current prices for a whole watchlist in one call.

API reference: https://docs.birdeye.so/reference/get-defi-multi_price

This is intentionally NOT the WebSocket API - see ADR-019 for why: real
WebSocket access requires Birdeye's Premium tier ($199/mo and up), while
multi_price is available on the free Standard tier. Polling every ~20-30s
is not true push-based real-time, but for a personal memecoin watchlist
the difference between "instant" and "20 seconds old" is not meaningfully
different in practice, and this keeps the project's cost at $0.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import RealtimePriceAdapter
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so/defi/multi_price"

# Birdeye's free Standard tier is rate-limited to 1 request/second overall
# (see docs/decisions.md ADR-019) - this batches an entire watchlist into
# ONE call regardless of size, so polling stays well under that limit even
# with a poll interval far shorter than our chosen default.
MAX_ADDRESSES_PER_CALL = 100


class BirdeyeRealtimePriceAdapter(RealtimePriceAdapter):
    source_name = "birdeye"

    def __init__(self, api_key: str, chain: str = "solana", max_retries: int = 3):
        self.api_key = api_key
        self.chain = chain
        self.max_retries = max_retries
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "x-chain": self.chain}

    def _get_with_backoff(self, params: dict) -> dict:
        """Same retry/backoff pattern as BirdeyeAdapter (historical OHLCV) -
        see that class's docstring for the full reasoning. Kept as a
        separate copy here rather than shared, since this adapter has a
        different base URL/response shape and premature sharing would
        couple two things that only coincidentally look similar today."""
        for attempt in range(self.max_retries):
            response = self._session.get(
                BASE_URL, headers=self._headers(), params=params, timeout=10
            )

            if response.status_code in (401, 403):
                raise PermissionError(
                    f"Birdeye auth failed ({response.status_code}) - check API key. "
                    "Not retrying."
                )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                delay = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Birdeye multi_price request failed (status=%s), "
                    "attempt %d/%d, retrying in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()

        raise RuntimeError(
            f"Birdeye multi_price failed after {self.max_retries} retries"
        )

    def fetch_latest_prices(
        self, token_addresses: list[str]
    ) -> Iterator[BronzeEnvelope]:
        if len(token_addresses) > MAX_ADDRESSES_PER_CALL:
            raise ValueError(
                f"Got {len(token_addresses)} addresses, Birdeye's multi_price "
                f"caps at {MAX_ADDRESSES_PER_CALL} per call. Batch your "
                "watchlist into multiple calls if you exceed this."
            )
        if not token_addresses:
            return

        params = {"list_address": ",".join(token_addresses)}
        data = self._get_with_backoff(params)
        prices = data.get("data", {})

        for address in token_addresses:
            entry = prices.get(address)
            if entry is None or entry.get("value") is None:
                # Docs explicitly note this happens for unknown/unsupported
                # tokens - skip quietly rather than raising, same
                # "one bad record doesn't kill the batch" principle as the
                # historical adapter.
                logger.warning(
                    "No price data returned for %s - skipping this poll", address
                )
                continue

            event_timestamp = datetime.now(timezone.utc)
            if entry.get("updateUnixTime"):
                event_timestamp = datetime.fromtimestamp(
                    entry["updateUnixTime"], tz=timezone.utc
                )

            yield BronzeEnvelope.build(
                source=self.source_name,
                token_address=address,
                event_timestamp=event_timestamp,
                domain="market",
                payload=entry,
            )
