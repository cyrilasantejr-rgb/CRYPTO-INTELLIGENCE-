"""
Birdeye implementation of MarketDataAdapter.

API reference: https://docs.birdeye.so/reference/get-defi-ohlcv
Endpoint caps responses at 1000 records per call, so a long date range at a
fine-grained interval must be split into multiple chunked requests.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import MarketDataAdapter
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

# Birdeye candle "type" -> seconds per candle. Used to compute how many candles
# a given date range will produce, so we know when to split into chunks.
INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "6H": 21600,
    "8H": 28800,
    "12H": 43200,
    "1D": 86400,
    "3D": 259200,
    "1W": 604800,
}

# Birdeye's hard cap is 1000 records/call. We request fewer than the max as a
# safety margin - the vendor's own limit is exactly 1000 records, and asking
# for slightly under that avoids off-by-one edge cases at chunk boundaries.
MAX_RECORDS_PER_REQUEST = 900

BASE_URL = "https://public-api.birdeye.so/defi/ohlcv"


class BirdeyeAdapter(MarketDataAdapter):
    source_name = "birdeye"

    def __init__(self, api_key: str, chain: str = "solana", max_retries: int = 5):
        self.api_key = api_key
        self.chain = chain
        self.max_retries = max_retries
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "x-chain": self.chain}

    def _get_with_backoff(self, params: dict) -> dict:
        """
        Perform a GET request with exponential backoff + jitter on transient
        failures.

        Why exponential backoff instead of a fixed retry delay: if the API is
        rate-limiting us or briefly overloaded, retrying every 1 second at
        fixed intervals just keeps hammering it at the same rate that caused
        the problem. Doubling the delay each attempt (1s, 2s, 4s, 8s...) backs
        off fast enough to actually relieve pressure. Random jitter is added
        so that if we had many parallel requests failing at once, they don't
        all retry at the exact same moment and cause a synchronized retry storm.

        401/403 (auth errors) are NOT retried - retrying a bad API key just
        wastes calls and time; that's a hard-stop configuration problem, not a
        transient failure.
        """
        for attempt in range(self.max_retries):
            response = self._session.get(
                BASE_URL, headers=self._headers(), params=params, timeout=15
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
                    "Birdeye request failed (status=%s), attempt %d/%d, "
                    "retrying in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            # Any other status: don't retry, fail loudly.
            response.raise_for_status()

        raise RuntimeError(
            f"Birdeye request failed after {self.max_retries} retries: {params}"
        )

    def fetch_historical_ohlcv(
        self,
        token_address: str,
        start: datetime,
        end: datetime,
        interval: str = "1H",
    ) -> Iterator[BronzeEnvelope]:
        if interval not in INTERVAL_SECONDS:
            raise ValueError(
                f"Unsupported interval '{interval}'. "
                f"Supported: {sorted(INTERVAL_SECONDS)}"
            )

        chunk_seconds = INTERVAL_SECONDS[interval] * MAX_RECORDS_PER_REQUEST
        chunk_start = start

        while chunk_start < end:
            candidate_end = datetime.fromtimestamp(
                chunk_start.timestamp() + chunk_seconds, tz=timezone.utc
            )
            chunk_end = min(candidate_end, end)

            params = {
                "address": token_address,
                "address_type": "token",
                "type": interval,
                "time_from": int(chunk_start.timestamp()),
                "time_to": int(chunk_end.timestamp()),
            }

            data = self._get_with_backoff(params)
            items = data.get("data", {}).get("items", [])

            for candle in items:
                try:
                    yield self._candle_to_envelope(token_address, candle)
                except (KeyError, ValueError) as exc:
                    # One malformed candle should never kill an entire
                    # historical backfill - log it and keep going.
                    logger.warning(
                        "Skipping malformed candle for %s: %s (%s)",
                        token_address,
                        candle,
                        exc,
                    )

            chunk_start = chunk_end

    def _candle_to_envelope(self, token_address: str, candle: dict) -> BronzeEnvelope:
        event_timestamp = datetime.fromtimestamp(candle["unixTime"], tz=timezone.utc)
        return BronzeEnvelope.build(
            source=self.source_name,
            token_address=token_address,
            event_timestamp=event_timestamp,
            domain="market",
            payload=candle,
        )
