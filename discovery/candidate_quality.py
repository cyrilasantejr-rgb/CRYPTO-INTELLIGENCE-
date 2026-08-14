"""
Data-quality check for discovery candidates: flags likely wash-traded /
manipulated tokens by their volume-to-liquidity ratio, before candidates
are treated as real signal anywhere downstream.

Mirrors the valid/quarantine split pattern established in
databricks/silver/market_silver.py (flatten_and_validate) - a pure
function, no I/O, returns (valid, quarantined) rather than silently
dropping records, per the project's DATA QUALITY spec ("impossible
volume" is explicitly named there).

Deliberately plain Python, not PySpark, unlike market_silver.py:
discovery batches are at most 100 records per call (Birdeye's own
`limit` cap), so there's no dataset-size reason to reach for a
distributed compute engine here. None of this project's other
per-domain quality logic (wallet_intelligence, rug_pull_intelligence)
uses Spark either - Spark is reserved for the bulk historical market
lakehouse pipeline where record counts are actually large.

This is NOT the rug-pull engine. It's a cheap, deterministic sanity
check on one ratio, run BEFORE anything reaches the real rug-pull /
wallet-graph / ML analysis in rug_pull_intelligence/. A candidate that
passes this check is not "safe" - it just is not obviously fake.
"""

from __future__ import annotations

import logging

from common.schemas.envelope import BronzeEnvelope

logger = logging.getLogger(__name__)

# Heuristic starting point, not empirically derived - see the same
# caveat on other thresholds in common/schemas/discovery_filters.py.
# A token trading 50x its own liquidity pool in 24h is far beyond what
# real, independent buyers/sellers can produce without catastrophic
# slippage on every trade; live testing surfaced real examples at
# 295x-6344x. 50x is deliberately conservative - it will let some
# borderline/aggressive-but-real activity through rather than risk
# quarantining legitimate high-turnover tokens. Tune once Phase 5's
# backtester can tell us what threshold actually correlates with rugs
# vs real momentum.
MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO = 50.0


def validate_candidates(
    candidates: list[BronzeEnvelope],
) -> tuple[list[BronzeEnvelope], list[BronzeEnvelope]]:
    """
    Split candidates into (valid, quarantined) based on volume/liquidity
    sanity. Pure function - no I/O, no network calls - so it is directly
    unit-testable with synthetic BronzeEnvelope objects, the same reason
    flatten_and_validate() in market_silver.py is split from its I/O
    orchestration.

    A candidate is quarantined if:
      - liquidity is missing, zero, or negative (volume against no
        liquidity is itself nonsensical, not just suspicious), OR
      - volume_24h_usd is missing or negative (an impossible value in
        its own right - see DATA QUALITY spec: "negative prices",
        "impossible volume"), OR
      - volume_24h_usd / liquidity exceeds MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO
    """
    valid: list[BronzeEnvelope] = []
    quarantined: list[BronzeEnvelope] = []

    for envelope in candidates:
        liquidity = envelope.payload.get("liquidity")
        volume_24h = envelope.payload.get("volume_24h_usd")

        if liquidity is None or liquidity <= 0:
            logger.warning(
                "Quarantined %s: missing/non-positive liquidity (%s)",
                envelope.token_address,
                liquidity,
            )
            quarantined.append(envelope)
            continue

        if volume_24h is None or volume_24h < 0:
            logger.warning(
                "Quarantined %s: missing/negative volume_24h_usd (%s)",
                envelope.token_address,
                volume_24h,
            )
            quarantined.append(envelope)
            continue

        ratio = volume_24h / liquidity
        if ratio > MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO:
            logger.warning(
                "Quarantined %s: volume/liquidity ratio %.1fx exceeds "
                "%.0fx sanity threshold (vol24h=$%.0f, liq=$%.0f) - "
                "likely wash trading",
                envelope.token_address,
                ratio,
                MAX_SANE_VOLUME_TO_LIQUIDITY_RATIO,
                volume_24h,
                liquidity,
            )
            quarantined.append(envelope)
            continue

        valid.append(envelope)

    return valid, quarantined
