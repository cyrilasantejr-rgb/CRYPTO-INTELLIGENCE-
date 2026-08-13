"""
Phase 11: fetches recent news via RSS (no API key needed - see ADR-031
for why this replaced the original CryptoPanic-based approach) and
classifies each item's event type and source credibility.

Usage:

    python3 -m news_intelligence.run_news_check --topic Solana
    python3 -m news_intelligence.run_news_check   # no filter, all recent news
"""

from __future__ import annotations

import argparse
import logging

from news_intelligence.news_classification import classify_news_item
from news_intelligence.rss_news_adapter import RssNewsAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(topic: str | None) -> None:
    adapter = RssNewsAdapter()
    envelopes = adapter.fetch_recent_news(topic_keyword=topic, limit_per_feed=10)

    if not envelopes:
        logger.info("No news found%s.", f" for topic '{topic}'" if topic else "")
        return

    logger.info(
        "=== Recent news%s (%d items) ===",
        f": {topic}" if topic else "",
        len(envelopes),
    )

    for envelope in envelopes:
        title = envelope.payload.get("title", "(no title)")
        domain = envelope.payload.get("domain", "")

        classification = classify_news_item(title=title, domain=domain)

        logger.info("---")
        logger.info("Title: %s", title)
        logger.info("Source: %s (credibility: %s)", domain, classification.credibility)
        logger.info("Event type: %s", classification.event_type)
        if classification.matched_keywords:
            logger.info("Matched keywords: %s", classification.matched_keywords)


def main() -> None:
    parser = argparse.ArgumentParser(description="News check (RSS-based)")
    parser.add_argument(
        "--topic", required=False, default=None, help="Filter by keyword, e.g. Solana"
    )
    args = parser.parse_args()
    run(args.topic)


if __name__ == "__main__":
    main()
