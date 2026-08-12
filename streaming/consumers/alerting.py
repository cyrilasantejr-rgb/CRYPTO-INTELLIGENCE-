"""
Pure rolling-window price-move detection - no Kafka, no I/O, no network.
This is deliberately separated from streaming/consumers/market_price_consumer.py
(the orchestration layer that actually reads from Kafka) for the same
reason every other phase in this project splits pure logic from I/O: it's
what actually gets unit tested thoroughly and quickly, and the orchestration
layer becomes thin, boring glue that's hard to get wrong.

Severity tiers loosely follow the ALERT ENGINE severities from the
project's original design (INFO/WATCH/WARNING/HIGH/CRITICAL), scaled to
percentage price moves within the rolling window.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

# (pct_change_threshold, severity) - checked in descending order, first
# match wins. A 25% move is also >20%, >10%, etc., but should be reported
# as CRITICAL, not the first (loosest) tier it happens to satisfy.
SEVERITY_THRESHOLDS = [
    (0.20, "CRITICAL"),
    (0.10, "HIGH"),
    (0.05, "WARNING"),
    (0.02, "WATCH"),
]


@dataclass
class PriceAlert:
    token_address: str
    severity: str
    pct_change: float
    window_start_price: float
    current_price: float
    window_start_time: datetime
    current_time: datetime


class PriceHistoryTracker:
    """
    Maintains a rolling window of recent (timestamp, price) observations
    per token, and detects when the price has moved enough within that
    window to warrant an alert.
    """

    def __init__(self, window: timedelta = timedelta(minutes=5)):
        self.window = window
        self._history: dict[str, deque[tuple[datetime, float]]] = {}

    def update(
        self, token_address: str, price: float, timestamp: datetime
    ) -> PriceAlert | None:
        """
        Records a new price observation and checks whether the move from
        the OLDEST price still inside the rolling window to this new price
        crosses an alert threshold. Returns None if no threshold is
        crossed (the common case - most polls don't warrant an alert).
        """
        history = self._history.setdefault(token_address, deque())
        history.append((timestamp, price))

        # Prune anything older than the window - keeps memory bounded and
        # ensures the comparison below is always against a genuinely
        # "within the last N minutes" reference point, not a stale one
        # from hours ago.
        cutoff = timestamp - self.window
        while history and history[0][0] < cutoff:
            history.popleft()

        if len(history) < 2:
            return None  # not enough history yet to compute a meaningful change

        window_start_time, window_start_price = history[0]
        if window_start_price == 0:
            return None  # avoid division by zero on a genuinely zero price

        pct_change = (price - window_start_price) / window_start_price

        for threshold, severity in SEVERITY_THRESHOLDS:
            if abs(pct_change) >= threshold:
                return PriceAlert(
                    token_address=token_address,
                    severity=severity,
                    pct_change=pct_change,
                    window_start_price=window_start_price,
                    current_price=price,
                    window_start_time=window_start_time,
                    current_time=timestamp,
                )

        return None
