from decision_engine.decision_logic import make_recommendation


def test_critical_risk_overrides_even_maximally_bullish_entry_signal():
    """The single most important property of this module: a CRITICAL
    security tier must override even a 0.99 (near-maximum) bullish
    entry-model probability. This is the whole point of building
    security overrides as an explicit, checked-first priority."""
    rec = make_recommendation(
        rug_risk_score=95,
        rug_risk_tier="CRITICAL",
        entry_model_probability=0.99,
    )
    assert rec.action == "AVOID"
    assert rec.override_triggered is True
    assert rec.would_emergency_exit_if_held is True
    assert any("OVERRIDE" in reason for reason in rec.reasons)


def test_very_high_risk_also_overrides():
    rec = make_recommendation(
        rug_risk_score=70,
        rug_risk_tier="VERY_HIGH",
        entry_model_probability=0.95,
    )
    assert rec.action == "AVOID"
    assert rec.override_triggered is True


def test_hack_news_overrides_bullish_entry_signal_even_from_unknown_source():
    """Per the module's design: even an unconfirmed report of a hack
    warrants caution until verified - credibility does not gate whether
    the override fires, only how it's annotated."""
    rec = make_recommendation(
        rug_risk_score=10,
        rug_risk_tier="LOW",
        entry_model_probability=0.95,
        news_event_type="hack",
        news_credibility="UNKNOWN",
    )
    assert rec.action == "AVOID"
    assert rec.override_triggered is True
    assert rec.would_emergency_exit_if_held is True


def test_benign_news_event_type_does_not_trigger_override():
    rec = make_recommendation(
        rug_risk_score=10,
        rug_risk_tier="LOW",
        entry_model_probability=0.8,
        news_event_type="partnership",
    )
    assert rec.override_triggered is False
    assert rec.action == "BUY"


def test_clean_bullish_signals_produce_buy():
    rec = make_recommendation(
        rug_risk_score=5,
        rug_risk_tier="LOW",
        entry_model_probability=0.75,
    )
    assert rec.action == "BUY"
    assert rec.override_triggered is False
    assert rec.confidence > 0.5


def test_missing_entry_model_produces_hold_not_buy_or_avoid():
    """Without an entry signal and without any override, there isn't
    enough basis to recommend BUY - must default to the cautious
    middle ground, not guess in either direction."""
    rec = make_recommendation(
        rug_risk_score=10,
        rug_risk_tier="LOW",
        entry_model_probability=None,
    )
    assert rec.action == "HOLD"
    assert "unavailable" in " ".join(rec.reasons).lower()


def test_bearish_entry_signal_produces_avoid_without_needing_override():
    rec = make_recommendation(
        rug_risk_score=15,
        rug_risk_tier="LOW",
        entry_model_probability=0.15,
    )
    assert rec.action == "AVOID"
    assert rec.override_triggered is False  # AVOID via normal signal, not override


def test_high_risk_tier_alone_produces_avoid_even_with_bullish_entry():
    """HIGH is below the override threshold (VERY_HIGH/CRITICAL only),
    but should still be enough, combined with normal signal weighing,
    to avoid recommending BUY."""
    rec = make_recommendation(
        rug_risk_score=55,
        rug_risk_tier="HIGH",
        entry_model_probability=0.7,
    )
    assert rec.action == "AVOID"
    assert rec.override_triggered is False


def test_ambiguous_entry_probability_produces_hold():
    rec = make_recommendation(
        rug_risk_score=10,
        rug_risk_tier="LOW",
        entry_model_probability=0.45,
    )
    assert rec.action == "HOLD"


def test_all_signals_missing_still_returns_a_safe_default():
    rec = make_recommendation(
        rug_risk_score=None,
        rug_risk_tier=None,
        entry_model_probability=None,
    )
    assert rec.action == "HOLD"
    assert rec.override_triggered is False


def test_buy_is_unreachable_when_entry_model_probability_is_always_none():
    """Locks in a precise, verified fact about run_decision_check.py's
    current behavior (it always passes entry_model_probability=None,
    per ADR-032) - with no override firing and no entry signal, BUY can
    never be produced, only AVOID or HOLD. This is checked across a
    representative sweep of risk tiers, not just one case, so this test
    would actually fail if the branch logic ever changed to make BUY
    reachable without an entry signal - which would be a real behavior
    change worth deliberately deciding on, not stumbling into."""
    for tier in (None, "LOW", "MODERATE", "HIGH"):
        rec = make_recommendation(
            rug_risk_score=10,
            rug_risk_tier=tier,
            entry_model_probability=None,
        )
        assert rec.action != "BUY", f"BUY was reachable with tier={tier}, entry=None"


def test_override_reasons_are_explicit_not_hidden_in_a_blended_score():
    """A person reading the output must be able to see EXACTLY why an
    override fired - matches the project's explainability requirement."""
    rec = make_recommendation(
        rug_risk_score=90,
        rug_risk_tier="CRITICAL",
        entry_model_probability=0.5,
    )
    assert len(rec.reasons) >= 1
    assert "CRITICAL" in rec.reasons[0]
    assert "90" in rec.reasons[0]
