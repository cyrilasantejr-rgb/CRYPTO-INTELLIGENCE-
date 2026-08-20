from datetime import datetime, timezone

from decision_engine.decision_logic import Recommendation
from watchlist.scan_logic import (
    rank_watchlist,
    severity_for_recommendation,
    to_gold_record,
)


def _rec(**overrides) -> Recommendation:
    defaults = dict(
        action="HOLD",
        confidence=0.4,
        reasons=[],
        risks=[],
        override_triggered=False,
        would_emergency_exit_if_held=False,
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def test_security_override_is_critical():
    rec = _rec(action="AVOID", confidence=0.9, override_triggered=True)
    assert severity_for_recommendation(rec) == "CRITICAL"


def test_news_override_is_high_not_critical():
    # decision_logic.py gives news-only overrides confidence=0.75 -
    # below the 0.85 threshold, so a single unconfirmed headline should
    # not carry the same urgency as a verified on-chain security signal.
    rec = _rec(action="AVOID", confidence=0.75, override_triggered=True)
    assert severity_for_recommendation(rec) == "HIGH"


def test_confident_buy_is_watch():
    rec = _rec(action="BUY", confidence=0.8)
    assert severity_for_recommendation(rec) == "WATCH"


def test_weak_buy_is_info():
    rec = _rec(action="BUY", confidence=0.55)
    assert severity_for_recommendation(rec) == "INFO"


def test_plain_hold_and_avoid_are_info():
    assert severity_for_recommendation(_rec(action="HOLD")) == "INFO"
    assert severity_for_recommendation(_rec(action="AVOID", confidence=0.6)) == "INFO"


def test_ranking_puts_critical_before_confident_buy():
    critical = _rec(action="AVOID", confidence=0.9, override_triggered=True)
    buy = _rec(action="BUY", confidence=0.95)  # higher raw confidence, lower severity
    entries = rank_watchlist([("BUY_TOKEN", buy), ("CRITICAL_TOKEN", critical)])
    assert entries[0].token_address == "CRITICAL_TOKEN"
    assert entries[1].token_address == "BUY_TOKEN"


def test_ranking_orders_by_confidence_within_same_severity():
    high_conf_buy = _rec(action="BUY", confidence=0.85)
    low_conf_buy = _rec(action="BUY", confidence=0.71)
    entries = rank_watchlist(
        [("LOW", low_conf_buy), ("HIGH", high_conf_buy)]
    )
    assert [e.token_address for e in entries] == ["HIGH", "LOW"]


def test_to_gold_record_flattens_reasons_and_risks():
    rec = _rec(
        action="BUY",
        confidence=0.8,
        reasons=["reason one", "reason two"],
        risks=["risk one"],
    )
    ts = datetime(2026, 8, 17, tzinfo=timezone.utc)
    record = to_gold_record(
        rank_watchlist([("TOKEN", rec)])[0], scan_timestamp=ts
    )
    assert record["reasons"] == "reason one; reason two"
    assert record["risks"] == "risk one"
    assert record["scan_timestamp"] == ts.isoformat()
    assert record["token_address"] == "TOKEN"
    assert record["severity"] == "WATCH"
