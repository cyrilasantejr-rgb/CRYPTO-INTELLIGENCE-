"""
Fetches an SPL Token mint account's on-chain data via standard Solana
JSON-RPC (getAccountInfo, jsonParsed encoding) - using Helius's RPC
endpoint (already set up in this project, free tier), but this is
standard Solana RPC behavior any provider would return identically. See
mint_authority.py's module docstring for why this is more trustworthy
than a third-party security API summary.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import requests

from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

RPC_URL_TEMPLATE = "https://mainnet.helius-rpc.com/?api-key={api_key}"


class SolanaMintInfoAdapter:
    source_name = "solana_rpc"

    def __init__(self, helius_api_key: str, max_retries: int = 3):
        self.rpc_url = RPC_URL_TEMPLATE.format(api_key=helius_api_key)
        self.max_retries = max_retries
        self._session = requests.Session()

    def _post_with_backoff(self, payload: dict) -> dict:
        """Same retry/backoff shape used throughout this project's
        adapters, adapted for JSON-RPC's POST-based protocol (unlike the
        REST GET adapters elsewhere) - errors here come back as HTTP 200
        with an 'error' field in the JSON body, per JSON-RPC convention,
        rather than as HTTP error status codes."""
        for attempt in range(self.max_retries):
            response = self._session.post(self.rpc_url, json=payload, timeout=15)

            if response.status_code in (401, 403):
                raise PermissionError(
                    f"Helius RPC auth failed ({response.status_code}) - check "
                    "API key. Not retrying."
                )

            if response.status_code == 200:
                body = response.json()
                if "error" in body:
                    logger.warning(
                        "Solana RPC returned an error for getAccountInfo: %s",
                        body["error"],
                    )
                return body

            if response.status_code == 429 or response.status_code >= 500:
                delay = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Helius RPC request failed (status=%s), attempt %d/%d, "
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
            f"Helius RPC request failed after {self.max_retries} retries"
        )

    def fetch_mint_info(self, token_address: str) -> BronzeEnvelope:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [token_address, {"encoding": "jsonParsed"}],
        }
        response_body = self._post_with_backoff(payload)

        return BronzeEnvelope.build(
            source=self.source_name,
            token_address=token_address,
            event_timestamp=datetime.now(timezone.utc),
            domain="security",
            payload=response_body,
        )
