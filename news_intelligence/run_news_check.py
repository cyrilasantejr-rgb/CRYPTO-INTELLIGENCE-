"""
Phase 11: fetches recent news for a token/currency and classifies each
item's event type and source credibility.

Usage:

    python3 -m news_intelligence.run_news_check --currency SOL
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from news_intelligence.cryptopanic_adapter import CryptoPanicNewsAdapter
from news_intelligence.news_classification import classify_news_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(currency: str) -> None:
    load_dotenv()
    token = os.environ.get("CRYPTOPANIC_API_TOKEN")
    if not token:
        raise RuntimeError("CRYPTOPANIC_API_TOKEN not set. Add it to your .env file.")

    adapter = CryptoPanicNewsAdapter(auth_token=token)
    envelopes = adapter.fetch_recent_news(currency, limit=10)

    if not envelopes:
        logger.info("No news found for %s.", currency)
        return

    logger.info("=== Recent news: %s (%d items) ===", currency, len(envelopes))

    for envelope in envelopes:
        title = envelope.payload.get("title", "(no title)")
        domain = envelope.payload.get("domain", "")
        if not domain:
            source_info = envelope.payload.get("source", {})
            domain = (
                source_info.get("domain", "") if isinstance(source_info, dict) else ""
            )

        classification = classify_news_item(title=title, domain=domain)

        logger.info("---")
        logger.info("Title: %s", title)
        logger.info(
            "Source: %s (credibility: %s)",
            domain or "unknown",
            classification.credibility,
        )
        logger.info("Event type: %s", classification.event_type)
        if classification.matched_keywords:
            logger.info("Matched keywords: %s", classification.matched_keywords)


def main() -> None:
    parser = argparse.ArgumentParser(description="News check (CryptoPanic)")
    parser.add_argument("--currency", required=True, help="Currency code, e.g. SOL")
    args = parser.parse_args()
    run(args.currency)


if __name__ == "__main__":
    main()
