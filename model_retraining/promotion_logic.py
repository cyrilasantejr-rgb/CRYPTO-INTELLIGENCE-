"""
Champion/challenger model promotion decision logic - no I/O, no MLflow
dependency here at all. Given two metric dicts (the current champion's
and a newly-trained challenger's), decides whether the challenger
should be promoted.

CORE REQUIREMENT, directly from the project's original design: "Never
automatically replace a better model with a worse model merely because
the newer model was trained more recently." This module's entire
purpose is enforcing that - a challenger is compared on its actual
metrics, never favored just for being newer.

WHY MULTIPLE CRITERIA, NOT JUST ONE METRIC: a model that improves
ROC-AUC by inflating extreme, overconfident predictions while getting
WORSE calibration (Brier score) is not actually a better model for a
system whose recommendations need to be trustworthy - the improvement
on one axis could be gaming the other. Requiring the challenger to not
regress meaningfully on EITHER metric prevents exactly this failure
mode, at the cost of being more conservative about promoting - a
deliberate tradeoff given this project's stated aversion to overclaiming
confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Minimum improvement in ROC-AUC required to promote - a challenger that's
# only marginally better isn't worth the risk of an untested model
# replacing a known one. 0.02 is a deliberately modest bar (not 0.10+),
# since even a small, real improvement is worth having once it clears
# noise - but it must be a real improvement, not a coin-flip difference.
MIN_ROC_AUC_IMPROVEMENT = 0.02

# How much WORSE (higher) the challenger's Brier score (calibration
# error) is allowed to be, even if ROC-AUC improved - prevents an
# "improved" model that's secretly become overconfident/miscalibrated
# from being promoted.
MAX_BRIER_SCORE_REGRESSION = 0.01


@dataclass
class PromotionDecision:
    promote: bool
    reasons: list[str] = field(default_factory=list)


def decide_promotion(
    champion_metrics: dict[str, float] | None,
    challenger_metrics: dict[str, float],
) -> PromotionDecision:
    """
    champion_metrics: None means there is no current champion yet (the
    very first model trained) - in that case the challenger is promoted
    automatically, since there's nothing to compare against and having
    SOME tracked model is strictly better than having none.

    Both metric dicts are expected to have 'roc_auc' and 'brier_score'
    keys at minimum - missing keys are treated as "cannot evaluate this
    criterion," not silently assumed to pass or fail, and the decision
    errs toward NOT promoting when a required metric is missing, since
    promoting on incomplete information is a bigger risk than staying
    with a known champion a little longer.
    """
    if champion_metrics is None:
        return PromotionDecision(
            promote=True, reasons=["No existing champion - promoting first model"]
        )

    champion_auc = champion_metrics.get("roc_auc")
    challenger_auc = challenger_metrics.get("roc_auc")
    champion_brier = champion_metrics.get("brier_score")
    challenger_brier = challenger_metrics.get("brier_score")

    if challenger_auc is None or champion_auc is None:
        return PromotionDecision(
            promote=False,
            reasons=[
                "Cannot compare: roc_auc missing from champion or challenger metrics"
            ],
        )

    reasons: list[str] = []
    auc_improvement = challenger_auc - champion_auc

    if auc_improvement < MIN_ROC_AUC_IMPROVEMENT:
        reasons.append(
            f"ROC-AUC improvement ({auc_improvement:+.4f}) is below the "
            f"required minimum ({MIN_ROC_AUC_IMPROVEMENT}) - challenger "
            f"is not clearly better, keeping current champion"
        )
        return PromotionDecision(promote=False, reasons=reasons)

    reasons.append(
        f"ROC-AUC improved by {auc_improvement:+.4f} "
        f"({champion_auc:.4f} -> {challenger_auc:.4f})"
    )

    if challenger_brier is not None and champion_brier is not None:
        brier_regression = challenger_brier - champion_brier
        if brier_regression > MAX_BRIER_SCORE_REGRESSION:
            reasons.append(
                f"REJECTED despite ROC-AUC improvement: calibration "
                f"(Brier score) regressed by {brier_regression:+.4f}, "
                f"exceeding the allowed tolerance "
                f"({MAX_BRIER_SCORE_REGRESSION}) - the challenger may be "
                f"gaming ROC-AUC via overconfident predictions"
            )
            return PromotionDecision(promote=False, reasons=reasons)
        reasons.append(
            f"Calibration (Brier score) did not regress meaningfully "
            f"({brier_regression:+.4f})"
        )
    else:
        reasons.append(
            "Brier score unavailable for one or both models - calibration "
            "check skipped, promotion decision based on ROC-AUC alone"
        )

    return PromotionDecision(promote=True, reasons=reasons)
