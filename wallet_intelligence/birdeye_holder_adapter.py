"""
Birdeye implementation of HolderDataAdapter, using GET /defi/v3/token/holder
with mode=wallet - groups token accounts by actual owner wallet, giving
true wallet-level concentration rather than fragmented token-account
counts (a single whale can hold tokens across multiple accounts).

API reference: https://docs.birdeye.so/reference/get-defi-v3-token-holder

Confirmed on Birdeye's free Standard tier (checked their Data
Accessibility table before building this - see ADR-021 for why that
check matters, this project already got burned twice tonight by
assuming tier access instead of checking it).
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import HolderDataAdapter
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so/defi/v3/token/holder"


class BirdeyeHolderAdapter(HolderDataAdapter):
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
                    "Birdeye holder request failed (status=%s), attempt %d/%d, "
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
            f"Birdeye holder request failed after {self.max_retries} retries"
        )

    def fetch_top_holders(self, token_address: str, limit: int = 100) -> BronzeEnvelope:
        params = {
            "address": token_address,
            "mode": "wallet",
            "limit": min(limit, 100),  # vendor caps at 100 per docs
            "offset": 0,
        }
        data = self._get_with_backoff(params)

        return BronzeEnvelope.build(
            source=self.source_name,
            token_address=token_address,
            event_timestamp=datetime.now(timezone.utc),
            domain="holder",
            payload=data.get("data", {}),
        )
