"""
Phase 15: pure logic for turning a batch of per-token Recommendations
(from decision_engine.decision_logic) into a ranked, severity-tagged
watchlist - the piece that was missing to go from "one token, checked
by hand" to "here's today's scan, here's what needs your attention."

Deliberately pure functions, no I/O - same split this project uses
everywhere (see candidate_quality.py, decision_logic.py): the orchestration
script (run_watchlist_scan.py) handles fetching candidates and calling
the decision engine; this module only reasons about Recommendation
objects it's handed, which makes it directly unit-testable with
synthetic Recommendations and no network/API mocking required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from decision_engine.decision_logic import Recommendation

# Mirrors the ALERT ENGINE severities from the project's original design
# (INFO/WATCH/WARNING/HIGH/CRITICAL). This is a DIFFERENT severity scale
# than streaming/consumers/alerting.py's PriceAlert - that one measures
# rolling price moves; this one measures "how much does this
# recommendation deserve your attention", which is a judgment about the
# recommendation itself, not a price delta.
SEVERITY_ORDER = ["CRITICAL", "HIGH", "WARNING", "WATCH", "INFO"]


@dataclass
class WatchlistEntry:
    token_address: str
    recommendation: Recommendation
    severity: str  # one of SEVERITY_ORDER, or None-equivalent "INFO" floor


def severity_for_recommendation(recommendation: Recommendation) -> str:
    """
    Maps a Recommendation to an alert severity. Deliberately separate
    from decision_logic.py's action/confidence fields: 'action' answers
    "what should you do", severity answers "how urgently should this
    interrupt you" - a BUY signal and a security-override AVOID are both
    important, but not equally urgent, and conflating them would bury
    genuine danger under routine opportunities in a scan of 20+ tokens.
    """
    if recommendation.override_triggered:
        # decision_logic.py sets confidence=0.9 for a verified on-chain
        # security-tier override and 0.75 for a single-news-report
        # override - reusing that existing distinction rather than
        # inventing a new signal, since it already encodes "how certain
        # is this override" (on-chain data vs. one unconfirmed headline).
        return "CRITICAL" if recommendation.confidence >= 0.85 else "HIGH"

    if recommendation.action == "BUY":
        return "WATCH" if recommendation.confidence >= 0.7 else "INFO"

    # Plain AVOID (no override) and HOLD are informational in a scan
    # context - worth showing, not worth interrupting anyone for.
    return "INFO"


def rank_watchlist(
    results: list[tuple[str, Recommendation]],
) -> list[WatchlistEntry]:
    """
    Sorts scan results by severity first (CRITICAL surfaces before a
    routine BUY, regardless of confidence), then by confidence
    descending within the same severity tier. This is what makes a
    20-token scan skimmable: the two or three things that actually
    matter today land at the top, not buried alphabetically or by
    whatever order the screener happened to return them in.
    """
    entries = [
        WatchlistEntry(
            token_address=token_address,
            recommendation=rec,
            severity=severity_for_recommendation(rec),
        )
        for token_address, rec in results
    ]
    return sorted(
        entries,
        key=lambda e: (
            SEVERITY_ORDER.index(e.severity),
            -e.recommendation.confidence,
        ),
    )


def to_gold_record(entry: WatchlistEntry, scan_timestamp: datetime | None = None) -> dict[str, Any]:
    """
    Flattens a WatchlistEntry into a single flat dict record suitable for
    the Gold layer (gold/watchlist_scans/) - lists (reasons/risks) are
    joined into single strings rather than kept as nested list columns,
    matching this project's preference for flat, model/dashboard-ready
    Gold tables over nested structures (see docs/data_dictionary.md).
    """
    ts = scan_timestamp or datetime.now(timezone.utc)
    rec = entry.recommendation
    return {
        "scan_timestamp": ts.isoformat(),
        "token_address": entry.token_address,
        "severity": entry.severity,
        "action": rec.action,
        "confidence": rec.confidence,
        "override_triggered": rec.override_triggered,
        "would_emergency_exit_if_held": rec.would_emergency_exit_if_held,
        "reasons": "; ".join(rec.reasons),
        "risks": "; ".join(rec.risks),
    }
