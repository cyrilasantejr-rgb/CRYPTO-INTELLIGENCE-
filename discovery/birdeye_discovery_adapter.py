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
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import TokenDiscoveryAdapter
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so/defi/v3/token/list"


@dataclass
class DiscoveryFilters:
    """
    Query parameters for the token screener. Every min_/max_ field is
    optional (None = do not send that filter to Birdeye at all) - see
    to_params() for why that distinction matters.

    Defaults target small-cap tokens with real trading activity, not
    the whole market. max_liquidity is what actually excludes blue
    chips (SOL, USDC) - that ceiling defines the universe FIRST. Only
    then does sort_by="volume_24h_usd" rank within it, by absolute
    dollar activity.

    An earlier version of this file sorted by volume_24h_change_percent
    (percent change) instead - reverted after live testing showed it is
    numerically unstable near a zero baseline: a token trading for the
    first time has ~$0 prior-period volume, so ANY volume at all reads
    as a multi-billion-percent change (confirmed live: 7,584,390,951%
    on a token whose entire trade history was ~4 hours old) and
    dominates the ranking regardless of real quality. Percent-change
    fields remain useful as FLOORS on a future filter, but should not
    be the primary sort key when the underlying baseline can be ~zero.

    min_volume_24h_usd, min_holder, and min_trade_24h_count remain as
    floors against dead pools and wash-trade noise. These thresholds
    are heuristic starting points, not empirically derived - expect to
    tune them once Phase 5's backtesting framework can evaluate which
    filter combinations actually preceded good entries historically.
    """

    sort_by: str = "volume_24h_usd"
    sort_type: str = "desc"
    min_liquidity: float | None = 5_000
    max_liquidity: float | None = 2_000_000
    min_volume_24h_usd: float | None = 10_000
    min_holder: int | None = 50
    min_trade_24h_count: int | None = 50
    limit: int = 50
    offset: int = 0
    chain: str = "solana"

    def to_params(self) -> dict:
        """
        Build the query-param dict for this request, OMITTING any
        field that is None.

        Why this matters: Birdeye's docs define min_liquidity etc. as
        "filter for records >= this value." If we sent min_liquidity=0
        instead of omitting it, that is still a valid, deliberate filter
        (0 is a real number) - but None means "the user did not ask to
        filter on this at all," which is a different thing. Sending a
        key with value None/null would either error or be misread by
        the vendor, so we must drop the key entirely, not send it empty.
        """
        params: dict = {
            "sort_by": self.sort_by,
            "sort_type": self.sort_type,
            "limit": min(self.limit, 100),  # vendor caps at 100 per docs
            "offset": self.offset,
        }
        optional = {
            "min_liquidity": self.min_liquidity,
            "max_liquidity": self.max_liquidity,
            "min_volume_24h_usd": self.min_volume_24h_usd,
            "min_holder": self.min_holder,
            "min_trade_24h_count": self.min_trade_24h_count,
        }
        for key, value in optional.items():
            if value is not None:
                params[key] = value
        return params


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
                domain="market",
                payload=item,
            )
