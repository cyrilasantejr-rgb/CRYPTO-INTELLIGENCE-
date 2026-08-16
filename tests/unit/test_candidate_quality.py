"""
Unit tests for discovery/candidate_quality.py.

All synthetic - no network calls, no API key needed, because
validate_candidates() is a pure function. Same reasoning as
market_silver.py's flatten_and_validate(): the logic worth testing
should never require spinning up real infrastructure to test it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from common.schemas.envelope import BronzeEnvelope
from discovery.candidate_quality import (
    MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO,
    validate_candidates,
)


def _make_candidate(address: str, liquidity, volume_24h) -> BronzeEnvelope:
    """Build a minimal synthetic candidate envelope for testing."""
    payload = {"address": address, "liquidity": liquidity, "volume_24h_usd": volume_24h}
    return BronzeEnvelope.build(
        source="test",
        token_address=address,
        event_timestamp=datetime.now(timezone.utc),
        domain="discovery",
        payload=payload,
    )


def test_normal_candidate_is_valid():
    # $50k volume on $20k liquidity = 2.5x - well within sane range
    candidate = _make_candidate("addr1", liquidity=20_000, volume_24h=50_000)
    valid, quarantined = validate_candidates([candidate])
    assert valid == [candidate]
    assert quarantined == []


def test_wash_trading_ratio_is_quarantined():
    # Real example from live testing: $81M volume on $12,884 liquidity
    candidate = _make_candidate("addr2", liquidity=12_884, volume_24h=81_413_719)
    valid, quarantined = validate_candidates([candidate])
    assert valid == []
    assert quarantined == [candidate]


def test_zero_liquidity_is_quarantined_not_crashed():
    # Must not raise ZeroDivisionError
    candidate = _make_candidate("addr3", liquidity=0, volume_24h=10_000)
    valid, quarantined = validate_candidates([candidate])
    assert valid == []
    assert quarantined == [candidate]


def test_missing_liquidity_field_is_quarantined():
    candidate = _make_candidate("addr4", liquidity=None, volume_24h=10_000)
    valid, quarantined = validate_candidates([candidate])
    assert valid == []
    assert quarantined == [candidate]


def test_negative_volume_is_quarantined():
    candidate = _make_candidate("addr5", liquidity=10_000, volume_24h=-500)
    valid, quarantined = validate_candidates([candidate])
    assert valid == []
    assert quarantined == [candidate]


def test_ratio_exactly_at_threshold_is_valid():
    # Boundary: exactly MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO should pass
    # (the check is "> threshold", not ">="), and just above should fail.
    liquidity = 1_000
    at_threshold = _make_candidate(
        "addr6",
        liquidity=liquidity,
        volume_24h=liquidity * MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO,
    )
    just_over = _make_candidate(
        "addr7",
        liquidity=liquidity,
        volume_24h=liquidity * MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO + 1,
    )
    valid, quarantined = validate_candidates([at_threshold, just_over])
    assert at_threshold in valid
    assert just_over in quarantined


def test_mixed_batch_splits_correctly():
    good = _make_candidate("addr8", liquidity=20_000, volume_24h=50_000)
    bad = _make_candidate("addr9", liquidity=12_884, volume_24h=81_413_719)
    valid, quarantined = validate_candidates([good, bad])
    assert valid == [good]
    assert quarantined == [bad]


def test_empty_input_returns_empty_lists():
    valid, quarantined = validate_candidates([])
    assert valid == []
    assert quarantined == []
