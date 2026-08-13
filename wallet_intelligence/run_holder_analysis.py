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
from wallet_intelligence.holder_concentration import (
    classify_risk_tier,
    compute_concentration_metrics,
)

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

    # Sample-relative metrics: concentration AMONG the fetched top-100
    # holders, not against total supply - see ADR-024 for why this
    # matters. Useful as a supplementary "how lopsided is ownership even
    # among the whales themselves" signal, but NOT the number to use for
    # an actual risk verdict.
    sample_metrics = compute_concentration_metrics(amounts)

    # Authoritative metric: Birdeye's own top10_hold_percent IS computed
    # against real total circulating supply - this is the number that
    # actually answers "is this token dangerously concentrated?" and is
    # what risk_tier below is based on.
    vendor_top10_pct = envelope.payload.get("top10_hold_percent")
    total_holders = envelope.payload.get("holder")

    logger.info("=== Holder concentration: %s ===", token_address)
    if total_holders is not None:
        logger.info("Total holders (all wallets): %s", f"{total_holders:,}")
    logger.info("Holders sampled for this analysis: %d", sample_metrics.holder_count)

    if vendor_top10_pct is not None:
        # Birdeye reports this as a percentage number already (e.g. 1.2
        # meaning 1.2%), not a 0-1 fraction.
        vendor_top10_fraction = vendor_top10_pct / 100
        risk_tier = classify_risk_tier(vendor_top10_fraction)
        logger.info("Top-10 concentration (of total supply): %.2f%%", vendor_top10_pct)
        logger.info("Risk tier: %s", risk_tier)
    else:
        logger.warning(
            "Birdeye did not report top10_hold_percent - falling back to "
            "sample-relative risk tier, which is NOT directly comparable "
            "to a total-supply-based assessment."
        )
        logger.info(
            "Risk tier (sample-relative, less reliable): %s", sample_metrics.risk_tier
        )

    logger.info(
        "Concentration among sampled top-100 holders: top10=%.1f%%, HHI=%.4f "
        "(supplementary signal, not the risk verdict - see above)",
        sample_metrics.top10_concentration_pct * 100,
        sample_metrics.hhi,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holder concentration analysis (Birdeye)"
    )
    parser.add_argument("--token", required=True, help="Solana token mint address")
    args = parser.parse_args()
    run(args.token)


if __name__ == "__main__":
    main()
