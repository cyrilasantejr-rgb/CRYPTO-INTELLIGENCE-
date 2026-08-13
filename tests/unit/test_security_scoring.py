from rug_pull_intelligence.security_scoring import compute_rug_risk_score


def test_clean_token_scores_low_with_no_reasons_flagged():
    assessment = compute_rug_risk_score(
        holder_concentration_tier="LOW",
        has_recent_dev_outflow=False,
        mint_authority_active=False,
        freeze_authority_active=False,
    )
    assert assessment.rug_risk_score == 0
    assert assessment.risk_tier == "LOW"
    assert "No red flags" in assessment.reasons[0]
    assert assessment.data_gaps == []


def test_single_red_flag_alone_does_not_reach_critical():
    """A single bad signal (even the worst tier alone) should not, by
    itself, be able to push an otherwise-clean token to CRITICAL - the
    whole point of combining multiple signals is that no one signal
    being wrong can single-handedly produce the most severe verdict."""
    assessment = compute_rug_risk_score(
        holder_concentration_tier="CRITICAL",
        has_recent_dev_outflow=False,
        mint_authority_active=False,
        freeze_authority_active=False,
    )
    assert assessment.rug_risk_score == 50
    assert assessment.risk_tier != "CRITICAL"


def test_multiple_red_flags_compound_to_critical():
    assessment = compute_rug_risk_score(
        holder_concentration_tier="CRITICAL",
        has_recent_dev_outflow=True,
        mint_authority_active=True,
        freeze_authority_active=True,
    )
    assert assessment.rug_risk_score == 100  # capped, even though raw sum is 110
    assert assessment.risk_tier == "CRITICAL"
    assert len(assessment.reasons) == 4


def test_missing_signal_contributes_zero_not_worst_case():
    """A signal that couldn't be fetched (API failure, missing field)
    must contribute NOTHING to the score - never assumed to be the
    worst-case value just because it's unknown."""
    assessment = compute_rug_risk_score(
        holder_concentration_tier=None,
        has_recent_dev_outflow=False,
        mint_authority_active=False,
        freeze_authority_active=False,
    )
    assert assessment.rug_risk_score == 0
    assert "holder concentration data unavailable" in assessment.data_gaps


def test_missing_signal_is_recorded_in_data_gaps_not_silently_dropped():
    assessment = compute_rug_risk_score(
        holder_concentration_tier="LOW",
        has_recent_dev_outflow=None,
        mint_authority_active=None,
        freeze_authority_active=False,
    )
    assert "dev-wallet outflow data unavailable" in assessment.data_gaps
    assert "mint authority status unavailable" in assessment.data_gaps
    assert "freeze authority status unavailable" not in assessment.data_gaps


def test_all_signals_missing_scores_zero_with_all_gaps_recorded():
    """The degenerate case: every single signal failed to fetch. Score
    must be 0 (not crash, not assume worst case), and all four gaps
    must be recorded so the caller knows this is an unreliable
    assessment, not a genuinely clean token."""
    assessment = compute_rug_risk_score(
        holder_concentration_tier=None,
        has_recent_dev_outflow=None,
        mint_authority_active=None,
        freeze_authority_active=None,
    )
    assert assessment.rug_risk_score == 0
    assert len(assessment.data_gaps) == 4


def test_moderate_concentration_plus_dev_outflow_reaches_moderate_tier():
    assessment = compute_rug_risk_score(
        holder_concentration_tier="MODERATE",
        has_recent_dev_outflow=True,
        mint_authority_active=False,
        freeze_authority_active=False,
    )
    # 15 (moderate concentration) + 25 (dev outflow) = 40
    assert assessment.rug_risk_score == 40
    assert assessment.risk_tier == "MODERATE"


def test_reasons_list_is_explainable_not_a_black_box():
    """Every point-contributing signal must show up as a human-readable
    reason - this is a core project value (every recommendation must be
    explainable), not just a nice-to-have."""
    assessment = compute_rug_risk_score(
        holder_concentration_tier="HIGH",
        has_recent_dev_outflow=True,
        mint_authority_active=True,
        freeze_authority_active=False,
    )
    joined_reasons = " ".join(assessment.reasons)
    assert "concentration" in joined_reasons.lower()
    assert (
        "outflow" in joined_reasons.lower() or "sent tokens" in joined_reasons.lower()
    )
    assert "mint authority" in joined_reasons.lower()
