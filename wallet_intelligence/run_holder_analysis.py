"""
Phase 9: fetches top holders for a token and prints a concentration
analysis. A standalone check-one-token-right-now script, not yet wired
into the batch pipeline or Airflow DAG - see docs/phase9_holder_setup.md
for why that's a deliberate, incremental next step rather than doing
everything in one pass.

Usage:

    python3 -m wallet_intelligence.run_holder_analysis --token <address>
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from wallet_intelligence.birdeye_holder_adapter import BirdeyeHolderAdapter
from wallet_intelligence.holder_concentration import compute_concentration_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(token_address: str) -> None:
    load_dotenv()
    api_key = os.environ.get("BIRDEYE_API_KEY")
    if not api_key:
        raise RuntimeError("BIRDEYE_API_KEY not set. Copy .env.example to .env.")

    adapter = BirdeyeHolderAdapter(api_key=api_key)
    envelope = adapter.fetch_top_holders(token_address, limit=100)

    items = envelope.payload.get("items", [])
    amounts = [
        item.get("amount", 0.0) for item in items if item.get("amount") is not None
    ]

    if not amounts:
        logger.warning(
            "No holder amounts returned for %s - cannot compute concentration. "
            "Raw payload: %s",
            token_address,
            envelope.payload,
        )
        return

    metrics = compute_concentration_metrics(amounts)

    vendor_top10 = envelope.payload.get("top10_hold_percent")

    logger.info("=== Holder concentration: %s ===", token_address)
    logger.info("Holders analyzed: %d", metrics.holder_count)
    logger.info(
        "Top-10 concentration (computed): %.1f%%", metrics.top10_concentration_pct * 100
    )
    if vendor_top10 is not None:
        logger.info("Top-10 concentration (Birdeye-reported): %.1f%%", vendor_top10)
    logger.info("HHI: %.4f", metrics.hhi)
    logger.info("Risk tier: %s", metrics.risk_tier)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holder concentration analysis (Birdeye)"
    )
    parser.add_argument("--token", required=True, help="Solana token mint address")
    args = parser.parse_args()
    run(args.token)


if __name__ == "__main__":
    main()
