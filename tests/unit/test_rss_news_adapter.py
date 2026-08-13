from unittest.mock import patch

import feedparser

from news_intelligence.rss_news_adapter import RssNewsAdapter, domain_from_url

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Crypto Feed</title>
    <item>
      <title>Solana partners with major fintech firm</title>
      <link>https://example.com/article1</link>
      <description>A new partnership was announced today.</description>
      <pubDate>Thu, 13 Aug 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Bitcoin price analysis for the week</title>
      <link>https://example.com/article2</link>
      <description>BTC continues to trade sideways.</description>
      <pubDate>Thu, 13 Aug 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_fetch_recent_news_parses_real_rss_correctly():
    """Uses feedparser against a real, minimal, well-formed RSS string -
    not a mock of feedparser's output - so this actually exercises
    feedparser's real parsing behavior, the part of this adapter most
    worth verifying given no live network access."""
    adapter = RssNewsAdapter()

    with patch.object(feedparser, "parse", return_value=feedparser.parse(SAMPLE_RSS)):
        envelopes = adapter.fetch_recent_news()

    # DEFAULT_FEEDS has 2 entries, each patched call returns the same
    # 2-item sample feed, so 4 total envelopes expected.
    assert len(envelopes) == 4
    titles = [e.payload["title"] for e in envelopes]
    assert "Solana partners with major fintech firm" in titles


def test_topic_keyword_filters_entries():
    adapter = RssNewsAdapter()

    with patch.object(feedparser, "parse", return_value=feedparser.parse(SAMPLE_RSS)):
        envelopes = adapter.fetch_recent_news(topic_keyword="Solana")

    assert len(envelopes) == 2  # matches from both feeds, one match each
    for envelope in envelopes:
        assert "solana" in envelope.payload["title"].lower()


def test_topic_keyword_filtering_is_case_insensitive():
    adapter = RssNewsAdapter()

    with patch.object(feedparser, "parse", return_value=feedparser.parse(SAMPLE_RSS)):
        envelopes = adapter.fetch_recent_news(topic_keyword="SOLANA")

    assert len(envelopes) == 2


def test_no_matching_keyword_returns_empty_list():
    adapter = RssNewsAdapter()

    with patch.object(feedparser, "parse", return_value=feedparser.parse(SAMPLE_RSS)):
        envelopes = adapter.fetch_recent_news(topic_keyword="Dogecoin")

    assert envelopes == []


def test_limit_per_feed_is_respected():
    adapter = RssNewsAdapter()

    with patch.object(feedparser, "parse", return_value=feedparser.parse(SAMPLE_RSS)):
        envelopes = adapter.fetch_recent_news(limit_per_feed=1)

    # 1 entry per feed x 2 feeds = 2
    assert len(envelopes) == 2


def test_one_feed_failing_does_not_block_the_other():
    """If fetching/parsing one feed raises, the adapter should still
    return results from the feeds that succeeded - one bad source
    shouldn't block everything else, same principle used throughout
    this project's adapters."""
    adapter = RssNewsAdapter()
    good_feed = feedparser.parse(SAMPLE_RSS)

    call_count = {"n": 0}

    def side_effect(url):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("simulated network failure")
        return good_feed

    with patch.object(feedparser, "parse", side_effect=side_effect):
        envelopes = adapter.fetch_recent_news()

    # First feed failed entirely, second feed's 2 entries still returned
    assert len(envelopes) == 2


def test_domain_from_url_strips_www_and_path():
    assert domain_from_url("https://www.coindesk.com/markets/article-123") == (
        "coindesk.com"
    )


def test_domain_from_url_handles_no_www():
    assert domain_from_url("https://cointelegraph.com/news/some-article") == (
        "cointelegraph.com"
    )
