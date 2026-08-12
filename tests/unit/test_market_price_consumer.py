from streaming.consumers.alerting import PriceHistoryTracker
from streaming.consumers.market_price_consumer import process_message


def make_envelope(token_address: str, price: float, event_timestamp: str):
    return {
        "token_address": token_address,
        "event_timestamp": event_timestamp,
        "payload": {"value": price},
    }


def test_process_message_updates_tracker_and_returns_none_for_first_message():
    tracker = PriceHistoryTracker()
    envelope = make_envelope("TokenA", 100.0, "2026-08-01T12:00:00+00:00")

    alert = process_message(envelope, tracker)

    assert alert is None


def test_process_message_returns_alert_on_big_move():
    tracker = PriceHistoryTracker()
    process_message(
        make_envelope("TokenA", 100.0, "2026-08-01T12:00:00+00:00"), tracker
    )

    alert = process_message(
        make_envelope("TokenA", 115.0, "2026-08-01T12:00:10+00:00"), tracker
    )

    assert alert is not None
    assert alert.severity == "HIGH"


def test_process_message_handles_missing_price_gracefully():
    tracker = PriceHistoryTracker()
    envelope = {
        "token_address": "TokenA",
        "event_timestamp": "2026-08-01T12:00:00+00:00",
        "payload": {},
    }

    alert = process_message(envelope, tracker)

    assert alert is None


def test_process_message_handles_z_suffix_timestamp():
    """Some JSON timestamps use trailing 'Z' instead of '+00:00' - both
    must parse correctly."""
    tracker = PriceHistoryTracker()
    process_message(make_envelope("TokenA", 100.0, "2026-08-01T12:00:00Z"), tracker)
    alert = process_message(
        make_envelope("TokenA", 120.0, "2026-08-01T12:00:05Z"), tracker
    )

    assert alert is not None
