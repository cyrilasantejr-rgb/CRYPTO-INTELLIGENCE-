"""
Deterministic momentum classification for discovery candidates.

This is deliberately NOT the entry ML model, and must never be
confused with it downstream. Phase 6's entry model estimates a learned
probability from historical patterns; this module just checks raw,
already-fetched screener metrics (volume, liquidity, holders, trade
count) against fixed thresholds - the same category of thing as
candidate_quality.py's wash-trading ratio check, not a prediction.

Why this exists: discovery/run_candidate_fetch.py already pulls these
fields from Birdeye for every candidate, but until now nothing
downstream looked at them again after the wash-trading quarantine
check - they were fetched, paid for (75 CU/request), and then
discarded. This makes real, already-available data usable by the
decision engine without pretending it's something it's not.

Thresholds below are heuristic starting points, not empirically
derived - same honest caveat as MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO in
candidate_quality.py. Tune once the backtester can measure what
actually correlates with good outcomes.
"""

from __future__ import annotations

# A candidate already passed the wash-trading quarantine filter to even
# reach this function (see candidate_quality.py) - these thresholds are
# about distinguishing "quiet but real" from "genuinely active" among
# already-legitimate-looking candidates, not about catching fakes again.
STRONG_MIN_VOLUME_24H_USD = 50_000
STRONG_MIN_LIQUIDITY_USD = 20_000
STRONG_MIN_HOLDER_COUNT = 100
STRONG_MIN_TRADE_24H_COUNT = 200

MODERATE_MIN_VOLUME_24H_USD = 10_000
MODERATE_MIN_LIQUIDITY_USD = 5_000
MODERATE_MIN_HOLDER_COUNT = 50
MODERATE_MIN_TRADE_24H_COUNT = 50


def classify_momentum(
    *,
    volume_24h_usd: float | None,
    liquidity: float | None,
    holder: int | None,
    trade_24h_count: int | None,
) -> str | None:
    """
    Returns "STRONG", "MODERATE", or None ("weak/insufficient data").

    All four metrics must clear a tier's thresholds together - a token
    with huge volume but almost no holders is a different (more
    suspicious) shape than one that's genuinely active across all four
    dimensions at once, even though it already passed the wash-trading
    ratio check. Missing any metric falls through to the next tier down
    rather than assuming the best case, matching this project's
    established "missing data contributes nothing" principle.
    """
    values = (volume_24h_usd, liquidity, holder, trade_24h_count)
    if any(v is None for v in values):
        return None

    if (
        volume_24h_usd >= STRONG_MIN_VOLUME_24H_USD
        and liquidity >= STRONG_MIN_LIQUIDITY_USD
        and holder >= STRONG_MIN_HOLDER_COUNT
        and trade_24h_count >= STRONG_MIN_TRADE_24H_COUNT
    ):
        return "STRONG"

    if (
        volume_24h_usd >= MODERATE_MIN_VOLUME_24H_USD
        and liquidity >= MODERATE_MIN_LIQUIDITY_USD
        and holder >= MODERATE_MIN_HOLDER_COUNT
        and trade_24h_count >= MODERATE_MIN_TRADE_24H_COUNT
    ):
        return "MODERATE"

    return None
