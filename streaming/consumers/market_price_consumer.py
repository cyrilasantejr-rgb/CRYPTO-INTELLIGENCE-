"""
Phase 8: subscribes to market.price.raw.v1 and feeds each price update
through PriceHistoryTracker, logging an alert whenever a token's price
moves enough within the rolling window to cross a severity threshold.

Usage:

    python3 -m streaming.consumers.market_price_consumer

Runs forever (Ctrl+C to stop), reading whatever the producer publishes.

STATE NOTE: alert history lives in memory (PriceHistoryTracker), not
Redis or any persistent store - see ADR-019 for why this is a deliberate
scope decision, not an oversight. Restarting this consumer resets its
rolling-window history for every token.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from kafka import KafkaConsumer

from streaming.consumers.alerting import PriceAlert, PriceHistoryTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOPIC = "market.price.raw.v1"


def process_message(envelope: dict, tracker: PriceHistoryTracker) -> PriceAlert | None:
    """
    Pure-ish function (the only side effect is mutating `tracker`'s
    internal state, which is the whole point of a stateful tracker) that
    takes one already-deserialized Kafka message and returns an alert if
    one was triggered. Separated from the Kafka consume loop below so it
    can be unit tested with plain dicts - no real Kafka connection needed.
    """
    token_address = envelope["token_address"]
    payload = envelope.get("payload", {})
    price = payload.get("value")

    if price is None:
        logger.warning("Skipping message with no price value: %s", envelope)
        return None

    event_timestamp = datetime.fromisoformat(
        envelope["event_timestamp"].replace("Z", "+00:00")
    )
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)

    return tracker.update(token_address, price, event_timestamp)


def run() -> None:
    load_dotenv()
    bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=bootstrap_servers,
        # A fresh consumer group each run would re-read the whole topic
        # history every time; a stable group id lets Kafka remember our
        # position (offset) across restarts and only deliver new messages.
        group_id="market-price-alerting",
        auto_offset_reset="latest",
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    tracker = PriceHistoryTracker()
    logger.info("Listening on topic=%s for price updates...", TOPIC)

    for message in consumer:
        envelope = message.value
        alert = process_message(envelope, tracker)

        if alert is not None:
            logger.warning(
                "[%s] %s: %.2f%% move (%.6f -> %.6f) over %s",
                alert.severity,
                alert.token_address,
                alert.pct_change * 100,
                alert.window_start_price,
                alert.current_price,
                alert.current_time - alert.window_start_time,
            )
        else:
            price = envelope.get("payload", {}).get("value")
            logger.info("[%s] price update: %s", envelope.get("token_address"), price)


if __name__ == "__main__":
    run()
