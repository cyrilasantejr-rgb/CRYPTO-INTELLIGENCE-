"""
RSS-based news adapter, using `feedparser` (a mature, extremely stable
Python library - not a hand-rolled XML parser) against established
crypto news outlets' public RSS feeds.

WHY THIS INSTEAD OF ANOTHER VENDOR JSON API, given tonight's pattern of
guessed API shapes needing correction (Birdeye's holder/security
endpoints, CryptoPanic's URL structure, and finally CryptoPanic's free
tier being fully discontinued): RSS is a decades-old, standardized
format, not a vendor-specific evolving REST API surface. feedparser
normalizes RSS/Atom variations into a consistent set of fields
(entry.title, entry.link, entry.published, entry.summary) regardless of
which outlet's feed is being read - the uncertainty that caused several
fixes tonight (does THIS vendor use camelCase or snake_case, what's the
exact wrapper key) doesn't really apply here the same way, since
feedparser's interface is what's being relied on, not each outlet's raw
XML quirks.

No API key needed at all - a genuine, permanent free option, not
subject to a vendor discontinuing a free tier (as just happened with
CryptoPanic - see ADR-031).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser

from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

# A small, curated set of established outlets - matches the credibility
# list in news_classification.py's _HIGH_CREDIBILITY_DOMAINS, which is
# not a coincidence: these are exactly the sources this project already
# considers high-credibility, so classify_source_credibility() will
# correctly report HIGH for news fetched from these feeds.
DEFAULT_FEEDS = {
    "cointelegraph.com": "https://cointelegraph.com/rss",
    "coindesk.com": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}


class RssNewsAdapter:
    source_name = "rss"

    def fetch_recent_news(
        self, topic_keyword: str | None = None, limit_per_feed: int = 10
    ) -> list[BronzeEnvelope]:
        """
        Fetches recent entries from every feed in DEFAULT_FEEDS.

        topic_keyword: if given, only entries whose title or summary
        contain this keyword (case-insensitive) are returned - e.g.
        "Solana" to filter a general crypto feed down to Solana-relevant
        news. None returns everything (useful for general market-wide
        news scanning).
        """
        envelopes: list[BronzeEnvelope] = []

        for domain, feed_url in DEFAULT_FEEDS.items():
            try:
                parsed = feedparser.parse(feed_url)
            except Exception:
                logger.exception("Failed to fetch/parse feed for %s", domain)
                continue

            if parsed.bozo:
                # feedparser's own signal that the feed was malformed in
                # some way - logged, not fatal, since feedparser often
                # still recovers usable entries even from a slightly
                # malformed feed.
                logger.warning(
                    "Feed for %s parsed with warnings: %s",
                    domain,
                    parsed.bozo_exception,
                )

            entries = parsed.entries[:limit_per_feed]

            for entry in entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")

                if topic_keyword is not None:
                    haystack = f"{title} {summary}".lower()
                    if topic_keyword.lower() not in haystack:
                        continue

                event_timestamp = datetime.now(timezone.utc)
                published_parsed = entry.get("published_parsed")
                if published_parsed is not None:
                    try:
                        event_timestamp = datetime(
                            *published_parsed[:6], tzinfo=timezone.utc
                        )
                    except (TypeError, ValueError):
                        logger.warning(
                            "Could not convert published_parsed for entry: %s", title
                        )

                envelopes.append(
                    BronzeEnvelope.build(
                        source=self.source_name,
                        token_address=topic_keyword or domain,
                        event_timestamp=event_timestamp,
                        domain="news",
                        payload={
                            "title": title,
                            "summary": summary,
                            "link": entry.get("link", ""),
                            "domain": domain,
                        },
                    )
                )

        return envelopes


def domain_from_url(url: str) -> str:
    """Small helper: extract a bare domain from a full URL, stripping
    'www.' - used when a caller has a link but wants the domain for
    credibility classification."""
    return urlparse(url).netloc.removeprefix("www.")
