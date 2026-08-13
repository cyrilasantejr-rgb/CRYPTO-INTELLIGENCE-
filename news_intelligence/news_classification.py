"""
Pure news event-type classification and source-credibility scoring - no
I/O, no vendor-specific parsing, no ML/LLM. Deterministic keyword rules,
consistent with the same "deterministic rules first" approach used for
the Phase 10 security engine.

"Do not equate sentiment with credibility" (from the project's original
design) is the reason this module has TWO separate, independent
outputs, not one blended score: a news item from an unknown blog
screaming "SOL TO THE MOON" is high-sentiment, low-credibility. A terse,
neutral-toned announcement from a well-known outlet about a real
exploit is low-sentiment-intensity but high-credibility and highly
market-relevant. Blending these into one number would hide exactly the
distinction that matters for deciding how much to trust a given
headline.
"""

from __future__ import annotations

from dataclasses import dataclass

# Event categories from the project's original design. Keyword lists are
# intentionally simple and auditable (a human can read every rule) rather
# than an opaque model - matches the "deterministic rules" approach used
# for the security engine's mint/freeze authority checks. Checked in
# order; first match wins, ordered roughly by specificity/severity so a
# headline mentioning both "hack" and "partnership" is correctly
# classified by the more urgent signal.
_EVENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("hack", ["hack", "hacked", "hacker", "compromised"]),
    ("exploit", ["exploit", "exploited", "vulnerability", "drained"]),
    ("rug_allegations", ["rug pull", "rugpull", "rugged", "scam alert"]),
    ("security_incident", ["security incident", "breach", "unauthorized access"]),
    ("lawsuit", ["lawsuit", "sued", "sec charges", "class action"]),
    ("regulation", ["regulation", "regulator", "sec ", "compliance", "banned"]),
    ("delisting", ["delist", "delisting", "removed from"]),
    ("exchange_listing", ["lists ", "listing", "now available on", "added to"]),
    ("network_outage", ["outage", "downtime", "network down", "halted"]),
    ("whale_activity", ["whale", "large transfer", "moved $"]),
    ("partnership", ["partnership", "partners with", "collaborat"]),
    ("developer_announcement", ["roadmap", "mainnet", "testnet", "upgrade", "release"]),
    ("token_launch", ["launches", "launch of", "airdrop", "new token"]),
]

# Curated credibility tiers for well-known crypto news domains - a
# simple, auditable, deliberately conservative starting list, NOT an
# exhaustive or authoritative source-reputation database. A domain not
# in this list is treated as UNKNOWN, not automatically low-credibility
# - absence of a rating is different from a bad rating, same "missing
# data isn't the worst case" principle used throughout this project.
_HIGH_CREDIBILITY_DOMAINS = {
    "coindesk.com",
    "cointelegraph.com",
    "theblock.co",
    "decrypt.co",
    "reuters.com",
    "bloomberg.com",
}


@dataclass
class NewsClassification:
    event_type: str  # one of _EVENT_KEYWORDS' categories, or "other"
    credibility: str  # HIGH / UNKNOWN (see _HIGH_CREDIBILITY_DOMAINS note)
    matched_keywords: list[str]


def classify_event_type(title: str, description: str = "") -> tuple[str, list[str]]:
    """
    Returns (event_type, matched_keywords). event_type is 'other' if no
    rule matches - a real, common case (most routine news doesn't fit
    a special category), not an error.
    """
    text = f"{title} {description}".lower()

    for event_type, keywords in _EVENT_KEYWORDS:
        matched = [kw for kw in keywords if kw in text]
        if matched:
            return event_type, matched

    return "other", []


def classify_source_credibility(domain: str) -> str:
    """
    domain: the publishing source's domain, e.g. 'coindesk.com'. Case-
    insensitive, tolerates a leading 'www.' since that's a common source
    of false negatives when comparing domains literally.
    """
    normalized = domain.lower().removeprefix("www.")
    return "HIGH" if normalized in _HIGH_CREDIBILITY_DOMAINS else "UNKNOWN"


def classify_news_item(
    title: str, domain: str, description: str = ""
) -> NewsClassification:
    event_type, matched_keywords = classify_event_type(title, description)
    credibility = classify_source_credibility(domain)
    return NewsClassification(
        event_type=event_type,
        credibility=credibility,
        matched_keywords=matched_keywords,
    )
