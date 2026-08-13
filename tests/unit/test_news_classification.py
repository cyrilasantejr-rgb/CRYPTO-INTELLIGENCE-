from news_intelligence.news_classification import (
    classify_event_type,
    classify_news_item,
    classify_source_credibility,
)


def test_hack_headline_classified_correctly():
    event_type, matched = classify_event_type("Solana bridge hacked for $10M")
    assert event_type == "hack"
    assert "hacked" in matched


def test_exploit_takes_priority_over_less_specific_keywords():
    event_type, _ = classify_event_type(
        "Protocol exploited via flash loan vulnerability"
    )
    assert event_type == "exploit"


def test_rug_allegations_detected():
    event_type, _ = classify_event_type(
        "Community raises rug pull allegations against dev"
    )
    assert event_type == "rug_allegations"


def test_partnership_announcement_classified():
    event_type, _ = classify_event_type(
        "Solana Foundation partners with major fintech firm"
    )
    assert event_type == "partnership"


def test_unrelated_headline_returns_other_not_a_crash():
    event_type, matched = classify_event_type("Local weather remains sunny this week")
    assert event_type == "other"
    assert matched == []


def test_description_is_also_searched_not_just_title():
    """A vague title with the real signal in the body text must still
    be classified correctly - not every important detail is in the
    headline."""
    event_type, _ = classify_event_type(
        title="Update on recent protocol activity",
        description="Investigators confirmed the wallet was compromised in a hack",
    )
    assert event_type == "hack"


def test_case_insensitive_matching():
    event_type, _ = classify_event_type("SOLANA NETWORK OUTAGE REPORTED")
    assert event_type == "network_outage"


def test_known_high_credibility_domain():
    assert classify_source_credibility("coindesk.com") == "HIGH"


def test_www_prefix_does_not_cause_false_negative():
    assert classify_source_credibility("www.coindesk.com") == "HIGH"


def test_unknown_domain_is_unknown_not_assumed_low():
    """A domain not in the curated list is UNKNOWN, not automatically
    low-credibility - absence of a rating must not be treated as a
    negative rating."""
    assert classify_source_credibility("some-random-blog.xyz") == "UNKNOWN"


def test_domain_matching_is_case_insensitive():
    assert classify_source_credibility("CoinDesk.com") == "HIGH"


def test_classify_news_item_combines_both_dimensions_independently():
    """The whole point of ADR-level design here: sentiment/hype and
    credibility are independent axes. A hack headline from an unknown
    source should show event_type=hack AND credibility=UNKNOWN - neither
    dimension should influence the other."""
    result = classify_news_item(
        title="EXPLOSIVE: Token X just got hacked, insiders say",
        domain="some-random-blog.xyz",
    )
    assert result.event_type == "hack"
    assert result.credibility == "UNKNOWN"
