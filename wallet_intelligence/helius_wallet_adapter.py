"""
Helius implementation of WalletTransactionAdapter, using the Enhanced
Transactions "by address" REST endpoint - returns already-parsed,
human-readable transaction history (token transfers, transaction type,
timestamps) rather than raw Solana transactions that would need manual
instruction-level decoding.

API reference: https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactionsbyaddress

Confirmed free-tier available: Helius's free plan (1M credits/month, no
card required) covers this - unlike Birdeye, no tier-access surprise
found when checking (see ADR-019/021 for the Birdeye cases where that
assumption turned out wrong; checking first this time rather than
assuming, consistent with that lesson).
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from common.interfaces.source_adapter import WalletTransactionAdapter
from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

BASE_URL = "https://api-mainnet.helius-rpc.com/v0/addresses"


class HeliusWalletAdapter(WalletTransactionAdapter):
    source_name = "helius"

    def __init__(self, api_key: str, max_retries: int = 3):
        self.api_key = api_key
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get_with_backoff(self, url: str, params: dict) -> list:
        """Same retry/backoff pattern used throughout this project's
        adapters - see birdeye_adapter.py for the full reasoning."""
        for attempt in range(self.max_retries):
            response = self._session.get(url, params=params, timeout=15)

            if response.status_code in (401, 403):
                raise PermissionError(
                    f"Helius auth failed ({response.status_code}) - check API "
                    "key. Not retrying."
                )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429 or response.status_code >= 500:
                delay = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Helius request failed (status=%s), attempt %d/%d, "
                    "retrying in %.1fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()

        raise RuntimeError(f"Helius request failed after {self.max_retries} retries")

    def fetch_transactions(
        self, wallet_address: str, limit: int = 100
    ) -> BronzeEnvelope:
        url = f"{BASE_URL}/{wallet_address}/transactions"
        params = {"api-key": self.api_key, "limit": min(limit, 100)}
        transactions = self._get_with_backoff(url, params)

        return BronzeEnvelope.build(
            source=self.source_name,
            # NOTE: token_address here actually holds a WALLET address, not
            # a token mint - a minor imperfect fit with the existing schema
            # (whose docstring says "Solana mint address"), noted honestly
            # rather than silently stretched. Using this field anyway
            # because it's what downstream consumers (and the Kafka
            # partition key) key off of, and "which entity is this event
            # about" is the right semantic even if the field name doesn't
            # perfectly describe a wallet. Worth revisiting if this project
            # ever needs the schema itself to distinguish "subject is a
            # token" from "subject is a wallet."
            token_address=wallet_address,
            event_timestamp=datetime.now(timezone.utc),
            domain="wallet",
            payload={"transactions": transactions},
        )
