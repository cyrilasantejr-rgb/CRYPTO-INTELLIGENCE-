"""
Phase 8: polls Birdeye's multi_price endpoint on a fixed interval and
publishes each price update to Kafka (Redpanda locally), partitioned by
token_address as designed back in Phase 0's Kafka topic design.

Usage:

    python3 -m streaming.producers.market_price_producer

Runs forever (Ctrl+C to stop) - this is a long-running service, not a
one-shot script like the batch ingestion runners in earlier phases.
"""

from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv
from kafka import KafkaProducer

from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOPIC = "market.price.raw.v1"

# A poll interval this short would be wasteful/expensive against a paid,
# per-request-billed API, but Birdeye's multi_price call here batches the
# ENTIRE watchlist into one request regardless of how many tokens are in
# it - so polling every 20s costs the same "1 request per poll" whether
# the watchlist has 1 token or 100.
POLL_INTERVAL_SECONDS = 20

# Small watchlist to start - a real deployment would load this from a
# config file or database, not hardcode it. Kept simple and explicit here
# rather than adding configuration-loading machinery for a 1-token list.
WATCHLIST = [
    "So11111111111111111111111111111111111111112",  # wrapped SOL
]


def run() -> None:
    load_dotenv()
    api_key = os.environ.get("BIRDEYE_API_KEY")
    if not api_key:
        raise RuntimeError("BIRDEYE_API_KEY not set. Copy .env.example to .env.")

    adapter = BirdeyeRealtimePriceAdapter(api_key=api_key)

    bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        # Partition key: same token_address partitioning scheme designed
        # in docs/architecture.md's Kafka topic table - all events for one
        # token land on the same partition, preserving per-token order.
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: v.encode("utf-8"),
        retries=3,
    )

    logger.info(
        "Starting price polling: watchlist=%s, interval=%ds, topic=%s",
        WATCHLIST,
        POLL_INTERVAL_SECONDS,
        TOPIC,
    )

    try:
        while True:
            try:
                count = 0
                for envelope in adapter.fetch_latest_prices(WATCHLIST):
                    producer.send(
                        TOPIC,
                        key=envelope.token_address,
                        value=envelope.model_dump_json(),
                    )
                    count += 1
                producer.flush()
                logger.info("Published %d price update(s)", count)
            except Exception:
                # A single failed poll (network blip, rate limit, etc.)
                # should not kill the long-running service - log it and
                # try again on the next interval. This mirrors the
                # "one bad record doesn't kill the batch" principle
                # applied at the level of "one bad poll doesn't kill the
                # service."
                logger.exception("Poll failed, will retry next interval")

            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopping (Ctrl+C received)")
    finally:
        producer.close()


if __name__ == "__main__":
    run()
