from model_retraining.promotion_logic import decide_promotion


def test_no_existing_champion_promotes_automatically():
    decision = decide_promotion(
        champion_metrics=None,
        challenger_metrics={"roc_auc": 0.55, "brier_score": 0.2},
    )
    assert decision.promote is True
    assert "No existing champion" in decision.reasons[0]


def test_meaningfully_better_challenger_is_promoted():
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.55, "brier_score": 0.20},
        challenger_metrics={"roc_auc": 0.62, "brier_score": 0.18},
    )
    assert decision.promote is True


def test_marginally_better_challenger_is_not_promoted():
    """Below the minimum improvement threshold - a coin-flip-sized
    difference in ROC-AUC is not a reliable enough signal to replace a
    known model."""
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.55, "brier_score": 0.20},
        challenger_metrics={"roc_auc": 0.555, "brier_score": 0.20},
    )
    assert decision.promote is False


def test_worse_challenger_is_not_promoted():
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.60, "brier_score": 0.20},
        challenger_metrics={"roc_auc": 0.50, "brier_score": 0.20},
    )
    assert decision.promote is False


def test_never_promote_a_newer_but_worse_model():
    """The single most important property of this module, directly from
    the project's stated requirement: a challenger must never be
    promoted just for being newer if its actual metrics are worse."""
    champion = {"roc_auc": 0.70, "brier_score": 0.10}
    clearly_worse_challenger = {"roc_auc": 0.40, "brier_score": 0.30}
    decision = decide_promotion(champion, clearly_worse_challenger)
    assert decision.promote is False


def test_improved_auc_but_regressed_calibration_is_rejected():
    """This is the whole point of checking Brier score alongside
    ROC-AUC: a model that improves ROC-AUC by becoming overconfident/
    miscalibrated is not actually a better model for this system's
    purposes, and must be caught, not silently promoted on ROC-AUC
    alone."""
    champion = {"roc_auc": 0.55, "brier_score": 0.15}
    challenger_gaming_auc = {"roc_auc": 0.65, "brier_score": 0.30}
    decision = decide_promotion(champion, challenger_gaming_auc)
    assert decision.promote is False
    assert any("REJECTED" in r for r in decision.reasons)


def test_improved_auc_with_stable_calibration_is_promoted():
    champion = {"roc_auc": 0.55, "brier_score": 0.15}
    genuinely_better_challenger = {"roc_auc": 0.65, "brier_score": 0.14}
    decision = decide_promotion(champion, genuinely_better_challenger)
    assert decision.promote is True


def test_missing_roc_auc_prevents_promotion_not_silently_assumed():
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.55, "brier_score": 0.2},
        challenger_metrics={"brier_score": 0.1},  # no roc_auc key at all
    )
    assert decision.promote is False
    assert "missing" in decision.reasons[0].lower()


def test_missing_brier_score_skips_calibration_check_but_still_promotes_on_auc():
    """If Brier score isn't available for comparison, the promotion
    decision should still work based on ROC-AUC alone, not fail closed
    entirely - a missing SECONDARY signal shouldn't block a clear
    primary-signal improvement."""
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.50},
        challenger_metrics={"roc_auc": 0.60},
    )
    assert decision.promote is True
    assert any("unavailable" in r.lower() for r in decision.reasons)


def test_reasons_are_always_explainable_not_a_black_box():
    """Matches this project's consistent explainability requirement -
    every promotion decision must show WHY, not just true/false."""
    decision = decide_promotion(
        champion_metrics={"roc_auc": 0.55, "brier_score": 0.20},
        challenger_metrics={"roc_auc": 0.62, "brier_score": 0.18},
    )
    assert len(decision.reasons) >= 1
    joined = " ".join(decision.reasons)
    assert (
        "0.55" in joined or "0.62" in joined
    )  # actual numbers shown, not just a verdict
