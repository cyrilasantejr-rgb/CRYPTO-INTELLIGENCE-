"""
Birdeye implementation of TokenDiscoveryAdapter, using
GET /defi/v3/token/list - Birdeye's token screener endpoint. Filters
across liquidity, volume, holder count, and price change to surface
candidates BEFORE they are already trending, rather than reading off
an already-curated "trending" list.

API reference: https://docs.birdeye.so/reference/get-defi-v3-token-list

Confirmed on Birdeye's free Standard tier before building this (see
ADR-021 on why that check matters). Costs 75 CU per request regardless
of how many tokens are returned (limit only affects items per call,
not cost) - `limit` and `offset` do not change the CU bill, so page
generously rather than making many small calls.

First slice: no scoring, no ranking beyond what Birdeye's own
sort_by/sort_type provide, no ML, no social data. Just a clean,
filtered, verified feed of raw candidates into the Bronze layer.

DiscoveryFilters itself lives in common/schemas/discovery_filters.py,
not here - it's a vendor-agnostic concept the shared interface needs
to reference, so it can't live inside a vendor-specific adapter file.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import TokenDiscoveryAdapter
from common.schemas.discovery_filters import DiscoveryFilters
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so/defi/v3/token/list"


class BirdeyeDiscoveryAdapter(TokenDiscoveryAdapter):
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
        adapters - see birdeye_holder_adapter.py for the full reasoning."""
        for attempt in range(self.max_retries):
            response = self._session.get(
                BASE_URL, headers=self._headers(), params=params, timeout=15
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
                    "Birdeye discovery request failed (status=%s), attempt %d/%d, "
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
            f"Birdeye discovery request failed after {self.max_retries} retries"
        )

    def discover_candidates(self, filters: DiscoveryFilters) -> Iterator[BronzeEnvelope]:
        data = self._get_with_backoff(filters.to_params())

        items = data.get("data", {}).get("items")
        if items is None:
            logger.warning(
                "Unexpected response shape - no 'items' key under 'data'. "
                "Top-level keys: %s. Response 'data' keys: %s",
                list(data.keys()),
                list(data.get("data", {}).keys())
                if isinstance(data.get("data"), dict)
                else "N/A",
            )
            items = []

        for item in items:
            address = item.get("address")
            if not address:
                logger.warning("Skipping candidate with no address field: %s", item)
                continue

            yield BronzeEnvelope.build(
                source=self.source_name,
                token_address=address,
                event_timestamp=datetime.now(timezone.utc),
                domain="discovery",
                payload=item,
            )
