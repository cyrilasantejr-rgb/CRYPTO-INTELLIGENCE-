"""
The security engine's core: combines every signal this project has
built so far - holder concentration (Phase 9), dev-wallet outflow
detection (Phase 9), and token security metadata (mint/freeze authority,
this phase) - into ONE normalized RUG_RISK_SCORE (0-100), with an
explicit, human-readable list of contributing reasons.

This is deliberately where the "combine everything into one score" work
happens - see ADR-022 (holder concentration) and the dev-wallet module's
docstring, both of which explicitly deferred this exact combination to
here rather than doing it prematurely at the individual-signal level.

Scale matches the project's original design: 0-20 low, 21-40 moderate,
41-60 high, 61-80 very high, 81-100 critical.

"Do not treat any security API as an unquestionable source of truth" -
this module's job is to weigh MULTIPLE independent signals and explain
its reasoning, not to blindly trust any single input. Every signal that
contributes to the score is named explicitly in the output; there is no
hidden logic a person reading the output can't audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskAssessment:
    rug_risk_score: int  # 0-100
    risk_tier: str  # LOW / MODERATE / HIGH / VERY_HIGH / CRITICAL
    reasons: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)  # signals that were unavailable


# Points awarded per signal, capped so no single signal alone can push
# an otherwise-clean token straight to CRITICAL - deliberately requires
# multiple independent red flags to reach the highest tiers, since any
# ONE signal being wrong (a data error, a false positive) shouldn't be
# able to single-handedly produce a CRITICAL verdict.
HOLDER_CONCENTRATION_POINTS = {
    "LOW": 0,
    "MODERATE": 15,
    "HIGH": 30,
    "VERY_HIGH": 40,
    "CRITICAL": 50,
}
DEV_OUTFLOW_POINTS = 25
MINT_AUTHORITY_ACTIVE_POINTS = 20
FREEZE_AUTHORITY_ACTIVE_POINTS = 15


def _tier_for_score(score: int) -> str:
    if score >= 81:
        return "CRITICAL"
    if score >= 61:
        return "VERY_HIGH"
    if score >= 41:
        return "HIGH"
    if score >= 21:
        return "MODERATE"
    return "LOW"


def compute_rug_risk_score(
    *,
    holder_concentration_tier: str | None,
    has_recent_dev_outflow: bool | None,
    mint_authority_active: bool | None,
    freeze_authority_active: bool | None,
) -> RiskAssessment:
    """
    Every parameter accepts None to mean "this signal wasn't available"
    (an API call failed, a field wasn't found in a vendor response,
    etc.) - a missing signal contributes ZERO points (never assumed to
    be the worst case) but IS listed in data_gaps, so the final score is
    never silently treated as more complete than it actually is.
    """
    score = 0
    reasons: list[str] = []
    data_gaps: list[str] = []

    if holder_concentration_tier is not None:
        points = HOLDER_CONCENTRATION_POINTS.get(holder_concentration_tier, 0)
        score += points
        if points > 0:
            reasons.append(
                f"Holder concentration is {holder_concentration_tier} "
                f"(+{points} points)"
            )
    else:
        data_gaps.append("holder concentration data unavailable")

    if has_recent_dev_outflow is not None:
        if has_recent_dev_outflow:
            score += DEV_OUTFLOW_POINTS
            reasons.append(
                f"Monitored wallet sent tokens out within the last 24h "
                f"(+{DEV_OUTFLOW_POINTS} points)"
            )
    else:
        data_gaps.append("dev-wallet outflow data unavailable")

    if mint_authority_active is not None:
        if mint_authority_active:
            score += MINT_AUTHORITY_ACTIVE_POINTS
            reasons.append(
                f"Mint authority is still active - supply can be "
                f"increased at will (+{MINT_AUTHORITY_ACTIVE_POINTS} points)"
            )
    else:
        data_gaps.append("mint authority status unavailable")

    if freeze_authority_active is not None:
        if freeze_authority_active:
            score += FREEZE_AUTHORITY_ACTIVE_POINTS
            reasons.append(
                f"Freeze authority is still active - holder accounts can "
                f"be frozen (+{FREEZE_AUTHORITY_ACTIVE_POINTS} points)"
            )
    else:
        data_gaps.append("freeze authority status unavailable")

    score = min(score, 100)

    if not reasons:
        reasons.append("No red flags detected in the signals that were available")

    return RiskAssessment(
        rug_risk_score=score,
        risk_tier=_tier_for_score(score),
        reasons=reasons,
        data_gaps=data_gaps,
    )
