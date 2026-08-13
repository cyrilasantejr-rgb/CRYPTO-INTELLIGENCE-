"""
Birdeye implementation for fetching token security metadata (mint/freeze
authority status, and whatever other security signals Birdeye reports).

API reference: https://docs.birdeye.so/reference/get-defi-token_security

HONEST CAVEAT, stated upfront rather than discovered later: unlike most
other Birdeye endpoints used in this project, the exact response field
names for this endpoint are NOT confirmed - Birdeye's docs page renders
its example response via JavaScript that this project's tooling can't
execute, and after three separate real-vs-documented mismatches with
Birdeye tonight (WebSocket tier access, multi_price tier access, and
holder response field casing), there is no reason to assume this
endpoint's fields are exactly as commonly described elsewhere. This
adapter stores the FULL raw payload rather than pre-parsing specific
fields, so the (separate, pure) scoring logic in security_scoring.py can
defensively check several plausible field names and degrade gracefully
- "no confident signal" rather than crashing or silently misreading -
if none match. See ADR-026.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so/defi/token_security"


class BirdeyeSecurityAdapter:
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
                    "Birdeye security request failed (status=%s), attempt "
                    "%d/%d, retrying in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()

        raise RuntimeError(
            f"Birdeye security request failed after {self.max_retries} retries"
        )

    def fetch_security_info(self, token_address: str) -> BronzeEnvelope:
        """
        Returns a BronzeEnvelope (domain='security') wrapping the raw
        'data' payload from Birdeye, unparsed - see the module docstring
        for why this deliberately does NOT pre-extract specific fields.
        """
        params = {"address": token_address}
        response = self._get_with_backoff(params)
        raw_data = response.get("data", {}) or {}

        return BronzeEnvelope.build(
            source=self.source_name,
            token_address=token_address,
            event_timestamp=datetime.now(timezone.utc),
            domain="security",
            payload=raw_data,
        )
