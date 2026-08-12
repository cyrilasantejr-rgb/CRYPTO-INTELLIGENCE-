from datetime import datetime, timedelta, timezone

from streaming.consumers.alerting import PriceHistoryTracker


def t(seconds_offset: int) -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=seconds_offset
    )


def test_no_alert_on_first_observation():
    tracker = PriceHistoryTracker()
    alert = tracker.update("TokenA", 100.0, t(0))
    assert alert is None


def test_no_alert_for_small_move():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    alert = tracker.update("TokenA", 100.5, t(10))  # 0.5% move
    assert alert is None


def test_watch_alert_at_2_percent():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    alert = tracker.update("TokenA", 102.5, t(10))  # 2.5% move
    assert alert is not None
    assert alert.severity == "WATCH"
    assert alert.pct_change == 0.025


def test_warning_alert_at_5_percent():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    alert = tracker.update("TokenA", 106.0, t(10))
    assert alert.severity == "WARNING"


def test_critical_alert_at_25_percent_not_misclassified_as_watch():
    """A 25% move also technically satisfies the 2% WATCH threshold - must
    be reported as CRITICAL, the tightest matching tier, not the loosest."""
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    alert = tracker.update("TokenA", 125.0, t(10))
    assert alert.severity == "CRITICAL"


def test_negative_move_also_triggers_alert():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    alert = tracker.update("TokenA", 88.0, t(10))  # -12% move
    assert alert is not None
    assert alert.severity == "HIGH"
    assert alert.pct_change == -0.12


def test_old_observations_pruned_outside_window():
    """A price from 10 minutes ago, outside a 5-minute window, must not
    be used as the comparison baseline - otherwise a slow drift over
    hours would falsely look like a sudden move."""
    tracker = PriceHistoryTracker(window=timedelta(minutes=5))
    tracker.update("TokenA", 100.0, t(0))
    # 10 minutes later - far outside the window. The only in-window
    # observation at this point is this single new one.
    alert = tracker.update("TokenA", 200.0, t(600))
    # Only one point in the window now (the old one got pruned) - not
    # enough history to compute a change.
    assert alert is None


def test_tokens_tracked_independently():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 100.0, t(0))
    tracker.update("TokenB", 5.0, t(0))

    alert_a = tracker.update("TokenA", 110.0, t(10))  # 10% move
    alert_b = tracker.update("TokenB", 5.01, t(10))  # 0.2% move

    assert alert_a is not None
    assert alert_a.severity == "HIGH"
    assert alert_b is None


def test_zero_price_does_not_crash():
    tracker = PriceHistoryTracker()
    tracker.update("TokenA", 0.0, t(0))
    alert = tracker.update("TokenA", 5.0, t(10))
    assert alert is None  # can't compute a meaningful pct change from zero
