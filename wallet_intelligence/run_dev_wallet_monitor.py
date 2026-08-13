"""
Phase 9: monitors a wallet (e.g. a token's dev wallet) for recent
outflows of a specific token - is this wallet selling/moving out their
holdings?

Usage:

    python3 -m wallet_intelligence.run_dev_wallet_monitor \
        --wallet <dev_wallet_address> --token <token_mint_address>
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from wallet_intelligence.dev_wallet_monitor import analyze_outflows
from wallet_intelligence.helius_wallet_adapter import HeliusWalletAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(wallet_address: str, token_address: str) -> None:
    load_dotenv()
    api_key = os.environ.get("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("HELIUS_API_KEY not set. Add it to your .env file.")

    adapter = HeliusWalletAdapter(api_key=api_key)
    envelope = adapter.fetch_transactions(wallet_address, limit=100)

    transactions = envelope.payload.get("transactions", [])
    if not transactions:
        logger.info(
            "No transactions found for %s (or Helius returned an empty/"
            "unexpected response). Nothing to analyze.",
            wallet_address,
        )
        return

    summary = analyze_outflows(transactions, wallet_address, token_address)

    logger.info("=== Dev-wallet outflow analysis ===")
    logger.info("Wallet: %s", wallet_address)
    logger.info("Token monitored: %s", token_address)
    logger.info("Transactions examined: %d", len(transactions))
    logger.info("Outflow transactions found: %d", summary.outflow_transaction_count)
    logger.info("Total amount sent out: %s", summary.total_outflow_amount)
    if summary.most_recent_outflow is not None:
        logger.info("Most recent outflow: %s", summary.most_recent_outflow.isoformat())
    if summary.has_recent_outflow:
        logger.warning(
            "FLAG: this wallet has sent this token out within the last 24 "
            "hours - worth investigating whether this is a sell/dump."
        )
    else:
        logger.info("No outflows within the last 24 hours.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dev-wallet outflow monitor (Helius)")
    parser.add_argument("--wallet", required=True, help="Wallet address to monitor")
    parser.add_argument(
        "--token", required=True, help="Token mint address to watch for"
    )
    args = parser.parse_args()
    run(args.wallet, args.token)


if __name__ == "__main__":
    main()
