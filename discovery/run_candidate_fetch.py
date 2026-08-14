"""
CLI entry point for discovery/birdeye_discovery_adapter.py - manual/ad-hoc
runner for the token screener, first slice.

Defaults here mirror DiscoveryFilters: max_liquidity defines a
small-cap universe first, sort_by=volume_24h_usd ranks within it by
absolute dollar activity (not percent change - see DiscoveryFilters
docstring for why percent-change ranking near a zero baseline blew up
in live testing).

Usage:
    python3 -m discovery.run_candidate_fetch
    python3 -m discovery.run_candidate_fetch --min-liquidity 10000 --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

from common.schemas.discovery_filters import DiscoveryFilters
from discovery.birdeye_discovery_adapter import BirdeyeDiscoveryAdapter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch token discovery candidates from Birdeye"
    )
    parser.add_argument("--sort-by", default="volume_24h_usd")
    parser.add_argument("--sort-type", default="desc", choices=["asc", "desc"])
    parser.add_argument("--min-liquidity", type=float, default=5_000)
    parser.add_argument("--max-liquidity", type=float, default=2_000_000)
    parser.add_argument(
        "--min-volume-24h", type=float, default=10_000, dest="min_volume_24h_usd"
    )
    parser.add_argument("--min-holder", type=int, default=50)
    parser.add_argument(
        "--min-trade-24h", type=int, default=50, dest="min_trade_24h_count"
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--chain", default="solana")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    api_key = os.getenv("BIRDEYE_API_KEY")
    if not api_key:
        logger.error("BIRDEYE_API_KEY not set - check your .env file")
        return 1

    args = parse_args()
    filters = DiscoveryFilters(
        sort_by=args.sort_by,
        sort_type=args.sort_type,
        min_liquidity=args.min_liquidity,
        max_liquidity=args.max_liquidity,
        min_volume_24h_usd=args.min_volume_24h_usd,
        min_holder=args.min_holder,
        min_trade_24h_count=args.min_trade_24h_count,
        limit=args.limit,
        offset=args.offset,
        chain=args.chain,
    )
    logger.info("Querying Birdeye with filters: %s", filters)

    adapter = BirdeyeDiscoveryAdapter(api_key=api_key, chain=args.chain)

    try:
        candidates = list(adapter.discover_candidates(filters))
    except PermissionError as exc:
        logger.error("Auth/tier error: %s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("Request failed after retries: %s", exc)
        return 1

    logger.info("Received %d candidate(s)", len(candidates))

    if not candidates:
        logger.warning(
            "Zero candidates returned - either filters are too strict, or the "
            "response shape did not match what discover_candidates() expects "
            "(check the WARNING log above, if any)."
        )
        return 0

    print("\n--- First candidate, full raw payload (confirms real field names) ---")
    print(json.dumps(candidates[0].payload, indent=2, default=str))

    print(
        f"\n--- All {len(candidates)} candidates (address, symbol, volume_24h_usd, liquidity) ---"
    )
    for envelope in candidates:
        p = envelope.payload
        print(
            f"  {envelope.token_address}  "
            f"symbol={p.get('symbol', '?')}  "
            f"vol24h=${p.get('volume_24h_usd', 0):,.0f}  "
            f"liq=${p.get('liquidity', 0):,.0f}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
