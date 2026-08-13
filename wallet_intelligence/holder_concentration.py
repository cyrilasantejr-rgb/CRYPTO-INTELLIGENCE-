"""
Pure holder-concentration analysis - no I/O, no vendor-specific parsing.
Takes a plain list of holder balances and computes standard concentration
metrics from them. Deliberately decoupled from BirdeyeHolderAdapter so
this logic can be unit tested with synthetic data and, later, reused
against a different vendor's holder data without any changes here.

IMPORTANT - what population these metrics describe (see ADR-024): this
module computes concentration WITHIN WHATEVER LIST YOU GIVE IT. If you
pass in the top 100 holders' amounts, "top10_concentration_pct" means
"top 10 as a share of those 100 holders' combined total" - NOT "top 10
as a share of the token's total circulating supply." Those are very
different numbers. If you have the token's real total supply (or a
vendor-reported total-supply-based percentage, like Birdeye's own
top10_hold_percent), prefer that for actual risk assessment - this
module's output is a legitimate, different, supplementary signal about
concentration among the observed/sampled holders themselves, not a
substitute for a total-supply-based number.

Two metrics, not one, on purpose:
  - top10_concentration_pct: the standard "do the biggest holders in
    this set own most of it?" number.
  - HHI (Herfindahl-Hirschman Index): a standard concentration measure
    from economics/antitrust analysis - sum of squared ownership shares.
    Unlike top-10%, HHI is sensitive to concentration WITHIN the top 10
    too: 10 wallets holding 5% each looks identical to top10% but is a
    very different risk profile than 1 wallet holding 45% and 9 wallets
    holding 0.5% each - HHI tells those two apart, top10% alone doesn't.

This module deliberately does NOT produce a single blended 0-100 risk
score. Combining this signal with liquidity, mint authority, and other
security signals into one score is the Phase 10 security/decision
engine's job, not this module's - jamming everything into one formula
here would hide exactly the kind of nuance (see the HHI example above)
that makes concentration analysis actually useful.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tiers directly mirror the RUG_RISK_SCORE interpretation from the
# project's original design (0-20 low, 21-40 moderate, 41-60 high,
# 61-80 very high, 81-100 critical), applied to a top-10-concentration
# percentage - kept consistent with that scale rather than inventing a
# new one, since Phase 10's security engine will need to reason about
# this signal on the same scale as everything else.
_TIER_THRESHOLDS = [
    (0.80, "CRITICAL"),
    (0.60, "VERY_HIGH"),
    (0.40, "HIGH"),
    (0.20, "MODERATE"),
]


def classify_risk_tier(top10_concentration_pct: float) -> str:
    """
    Shared tier classifier, deliberately factored out so the SAME logic
    applies whether the top10 percentage came from this module's own
    sample-relative computation or from a vendor's total-supply-based
    number (see run_holder_analysis.py, which uses this against Birdeye's
    own top10_hold_percent for the actual risk verdict - see ADR-024).
    """
    for threshold, tier in _TIER_THRESHOLDS:
        if top10_concentration_pct >= threshold:
            return tier
    return "LOW"


@dataclass
class ConcentrationMetrics:
    holder_count: int
    top10_concentration_pct: float  # 0.0 to 1.0, relative to the GIVEN list's total
    hhi: float  # 0.0 (perfectly distributed) to 1.0 (single holder owns everything)
    risk_tier: (
        str  # LOW / MODERATE / HIGH / VERY_HIGH / CRITICAL, relative to the given list
    )


def compute_concentration_metrics(holder_amounts: list[float]) -> ConcentrationMetrics:
    """
    holder_amounts: raw token amounts (or USD values - any consistent
    unit works, since only relative shares matter) for each holder,
    already sorted or unsorted - this function sorts internally.

    An empty list is a valid input (a brand new token with no holders
    yet, or a failed/empty API response) and returns a "no data" result
    rather than raising - the caller decides whether that's itself a red
    flag worth surfacing.
    """
    if not holder_amounts:
        return ConcentrationMetrics(
            holder_count=0, top10_concentration_pct=0.0, hhi=0.0, risk_tier="LOW"
        )

    total = sum(holder_amounts)
    if total <= 0:
        return ConcentrationMetrics(
            holder_count=len(holder_amounts),
            top10_concentration_pct=0.0,
            hhi=0.0,
            risk_tier="LOW",
        )

    sorted_amounts = sorted(holder_amounts, reverse=True)
    shares = [amount / total for amount in sorted_amounts]

    top10_pct = sum(shares[:10])
    hhi = sum(share**2 for share in shares)

    return ConcentrationMetrics(
        holder_count=len(holder_amounts),
        top10_concentration_pct=top10_pct,
        hhi=hhi,
        risk_tier=classify_risk_tier(top10_pct),
    )
