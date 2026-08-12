"""
Birdeye implementation of RealtimePriceAdapter, using the single-token
/defi/price endpoint, called once per watchlist token.

API reference: https://docs.birdeye.so/reference/get-defi-price

WHY NOT /defi/multi_price (the batched endpoint): see ADR-021. Checked
Birdeye's actual "Data Accessibility by Packages" table before writing
this - /defi/multi_price requires the Lite tier ($39/mo) or above; the
free Standard tier used throughout this project has NO access to it at
all (confirmed empirically too: it returned 401 against a key that works
fine everywhere else). /defi/price (single-token) IS available on
Standard - the same tier already used for every other endpoint in this
project - so this adapter calls it once per token instead.
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

BASE_URL = "https://public-api.birdeye.so/defi/price"


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
        """Same retry/backoff pattern used throughout this project's
        adapters - see birdeye_adapter.py for the full reasoning."""
        for attempt in range(self.max_retries):
            response = self._session.get(
                BASE_URL, headers=self._headers(), params=params, timeout=10
            )

            if response.status_code in (401, 403):
                raise PermissionError(
                    f"Birdeye auth failed ({response.status_code}) - check API "
                    "key or account tier for this endpoint. Not retrying."
                )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                delay = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Birdeye price request failed (status=%s), attempt %d/%d, "
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
            f"Birdeye price request failed after {self.max_retries} retries"
        )

    def fetch_latest_prices(
        self, token_addresses: list[str]
    ) -> Iterator[BronzeEnvelope]:
        """
        One /defi/price call PER token - not a batched call. This is a
        real cost/scale tradeoff, not an oversight: it means N tokens in
        the watchlist means N API calls per poll, whereas the (paid-tier-
        only) multi_price endpoint would do it in one call regardless of
        N. For a small personal watchlist (a handful of tokens) at a
        20-second poll interval, this is well within the free Standard
        tier's 1 request/second rate limit. If the watchlist grows large
        enough that this becomes a real constraint, that's the concrete
        signal to actually pay for Lite/Premium - not something to
        silently work around further on the free tier.
        """
        for address in token_addresses:
            params = {"address": address}
            try:
                data = self._get_with_backoff(params)
            except PermissionError:
                raise
            except RuntimeError:
                # _get_with_backoff exhausted its retries for this one
                # token - skip it and move on to the rest of the
                # watchlist this poll, rather than one bad token blocking
                # everyone else.
                logger.warning(
                    "Failed to fetch price for %s after retries - skipping "
                    "this token this poll",
                    address,
                )
                continue

            entry = data.get("data")
            if entry is None or entry.get("value") is None:
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
