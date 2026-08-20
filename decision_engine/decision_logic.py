"""
Phase 12 (first slice): combines the security engine (Phase 10), the
entry ML model's probability (Phase 6), and news classification
(Phase 11) into one final, explainable action recommendation.

SCOPE, stated plainly: only produces position-independent actions -
BUY, HOLD, AVOID - not the full action vocabulary from the project's
original design (ADD, TAKE_PARTIAL_PROFIT, REDUCE_POSITION, EXIT all
require knowing whether a position is already held, which needs Phase
13's paper-trading/position tracker, not built yet). When a CRITICAL
security risk is found, the recommendation explicitly notes this would
be an EMERGENCY_EXIT if a position were held - surfaced as information,
not returned as an actual action this module can't correctly compute
yet.

CORE DESIGN PRINCIPLE, directly from the project's original
requirements: "Do not treat any security API as an unquestionable
source of truth" and security overrides "must be explicit and
auditable." This module checks for override conditions FIRST, before
ever looking at the entry model's probability - a critical security
finding or a real hack/exploit headline can and does override even a
maximally bullish ML signal, and every override is stated as a named
reason in the output, never silently absorbed into a blended score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# News event types that represent an acute, currently-unfolding negative
# event - these trigger an override regardless of any other signal, same
# tier of severity as a CRITICAL rug-risk score. A "partnership" or
# "token_launch" headline should never override anything; a "hack" or
# "exploit" headline discovered right now should always be taken
# seriously regardless of how good other signals look.
ACUTE_NEGATIVE_EVENT_TYPES = {
    "hack",
    "exploit",
    "rug_allegations",
    "security_incident",
}

OVERRIDE_RISK_TIERS = {"CRITICAL", "VERY_HIGH"}


@dataclass
class Recommendation:
    action: str  # BUY / WATCHLIST / HOLD / AVOID
    confidence: float  # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    override_triggered: bool = False
    would_emergency_exit_if_held: bool = False


def make_recommendation(
    *,
    rug_risk_score: int | None,
    rug_risk_tier: str | None,
    entry_model_probability: float | None,
    news_event_type: str | None = None,
    news_credibility: str | None = None,
    discovery_momentum_tier: str | None = None,
) -> Recommendation:
    """
    All signals accept None to mean "unavailable" - same "missing data
    contributes nothing, never assumed worst case" principle as Phase
    10's security scoring, EXCEPT for the override checks below, which
    are a deliberate, narrow exception: a genuinely CRITICAL security
    signal or a credible acute-negative-news signal, when actually
    present, overrides regardless of what else is or isn't known. Missing
    signals don't trigger overrides; present, severe signals do.

    discovery_momentum_tier ("STRONG"/"MODERATE"/None, from
    discovery/momentum_signal.py) is a DETERMINISTIC screening
    heuristic on raw market metrics (volume, liquidity, holders, trade
    count) - explicitly NOT the ML entry model. It can only ever
    produce the WATCHLIST action, never BUY: while entry_model_probability
    is None (currently true for every candidate - see
    run_decision_check.py's docstring on why), there is no learned
    signal backing a genuine BUY recommendation, and this module will
    not manufacture one out of a threshold check. WATCHLIST means
    "passed real screening criteria, worth a manual look" - a narrower,
    more honest claim than BUY.
    """
    reasons: list[str] = []
    risks: list[str] = []

    # --- Priority 1: security-tier override, checked FIRST -----------
    if rug_risk_tier in OVERRIDE_RISK_TIERS:
        reasons.append(
            f"SECURITY OVERRIDE: rug-risk tier is {rug_risk_tier} "
            f"(score {rug_risk_score}/100) - this overrides any other "
            f"bullish signal"
        )
        return Recommendation(
            action="AVOID",
            confidence=0.9,
            reasons=reasons,
            risks=[f"Rug-risk tier: {rug_risk_tier}"],
            override_triggered=True,
            would_emergency_exit_if_held=True,
        )

    # --- Priority 2: acute negative news override ---------------------
    if news_event_type in ACUTE_NEGATIVE_EVENT_TYPES:
        credibility_note = (
            f" (source credibility: {news_credibility})"
            if news_credibility is not None
            else ""
        )
        reasons.append(
            f"SECURITY OVERRIDE: recent news classified as "
            f"'{news_event_type}'{credibility_note} - this overrides any "
            f"other bullish signal, regardless of source credibility, "
            f"since even an unconfirmed report of this severity warrants "
            f"caution until verified"
        )
        return Recommendation(
            action="AVOID",
            confidence=0.75,
            reasons=reasons,
            risks=[f"News event: {news_event_type}"],
            override_triggered=True,
            would_emergency_exit_if_held=True,
        )

    # --- No override - combine remaining signals normally -------------
    if rug_risk_tier == "HIGH":
        risks.append(f"Rug-risk tier is HIGH (score {rug_risk_score}/100)")
    elif rug_risk_tier == "MODERATE":
        risks.append(f"Rug-risk tier is MODERATE (score {rug_risk_score}/100)")
    elif rug_risk_tier == "LOW":
        reasons.append(f"Rug-risk tier is LOW (score {rug_risk_score}/100)")

    if entry_model_probability is None:
        reasons.append("Entry model probability unavailable")
        if discovery_momentum_tier == "STRONG" and rug_risk_tier in (
            None,
            "LOW",
            "MODERATE",
        ):
            reasons.append(
                "Discovery screening metrics (volume, liquidity, holders, "
                "trade count) are strong - this is a raw-data heuristic, "
                "NOT a model prediction, and does not by itself justify BUY"
            )
            action = "WATCHLIST"
            confidence = 0.5
        else:
            action = "HOLD"
            confidence = 0.3
    elif entry_model_probability >= 0.6 and rug_risk_tier in (None, "LOW", "MODERATE"):
        reasons.append(
            f"Entry model probability is {entry_model_probability:.2f} (bullish)"
        )
        action = "BUY"
        confidence = min(entry_model_probability, 0.85)
    elif entry_model_probability <= 0.3 or rug_risk_tier == "HIGH":
        if entry_model_probability <= 0.3:
            risks.append(
                f"Entry model probability is {entry_model_probability:.2f} (bearish)"
            )
        action = "AVOID"
        confidence = 0.6
    else:
        reasons.append(
            f"Entry model probability is {entry_model_probability:.2f} "
            f"(not strongly bullish or bearish)"
        )
        action = "HOLD"
        confidence = 0.4

    if not reasons and not risks:
        reasons.append("No strong signals in either direction")

    return Recommendation(
        action=action,
        confidence=confidence,
        reasons=reasons,
        risks=risks,
        override_triggered=False,
        would_emergency_exit_if_held=False,
    )
