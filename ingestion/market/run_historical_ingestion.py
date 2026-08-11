"""
Runner for Phase 1: historical market ingestion.

Usage (run locally, NOT in this sandbox - it needs a real Birdeye API key and
network access to public-api.birdeye.so):

    python -m ingestion.market.run_historical_ingestion \\
        --token So11111111111111111111111111111111111111112 \\
        --days 30 \\
        --interval 1H

Writes newline-delimited JSON (one BronzeEnvelope per line) to
data/bronze/market/{token_address}_{date}.ndjson

Why NDJSON locally instead of writing straight to S3/Delta here: Phase 2 owns
the S3 Bronze write path. Keeping this script's output format simple and
inspectable (you can literally `cat` the file and read it) makes it easy to
verify Phase 1 works correctly in isolation before Phase 2 adds cloud
complexity on top.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from ingestion.market.birdeye_adapter import BirdeyeAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(token_address: str, days: int, interval: str, output_dir: Path) -> Path:
    load_dotenv()
    api_key = os.environ.get("BIRDEYE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "BIRDEYE_API_KEY not set. Copy .env.example to .env and fill it in."
        )

    adapter = BirdeyeAdapter(api_key=api_key)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{token_address}_{end.date().isoformat()}.ndjson"

    count = 0
    with out_path.open("w") as f:
        for envelope in adapter.fetch_historical_ohlcv(
            token_address=token_address, start=start, end=end, interval=interval
        ):
            f.write(envelope.model_dump_json() + "\n")
            count += 1

    logger.info("Wrote %d bronze market events to %s", count, out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Historical market ingestion (Birdeye)"
    )
    parser.add_argument("--token", required=True, help="Solana token mint address")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch")
    parser.add_argument(
        "--interval", default="1H", help="Candle interval, e.g. 1H, 15m"
    )
    parser.add_argument(
        "--output-dir", default="data/bronze/market", help="Local output directory"
    )
    args = parser.parse_args()

    run(args.token, args.days, args.interval, Path(args.output_dir))


if __name__ == "__main__":
    main()
