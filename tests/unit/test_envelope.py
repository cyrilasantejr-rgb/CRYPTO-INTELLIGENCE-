from datetime import datetime, timezone

from common.schemas.envelope import BronzeEnvelope


def test_build_produces_valid_envelope():
    env = BronzeEnvelope.build(
        source="birdeye",
        token_address="So1anaTokenAddress111111111111111111111111",
        event_timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        domain="market",
        payload={"o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 1000},
    )
    assert env.source == "birdeye"
    assert env.domain == "market"
    assert env.schema_version == "1.0"
    assert len(env.event_id) == 64  # sha256 hex digest length


def test_event_id_is_deterministic():
    """Same inputs must always produce the same event_id - this is what makes
    Silver-layer dedup possible without vendor-specific logic (ADR-004)."""
    kwargs = {
        "source": "birdeye",
        "token_address": "TokenA",
        "event_timestamp": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        "payload": {"o": 1.0, "c": 1.1},
    }
    id_1 = BronzeEnvelope.compute_event_id(**kwargs)
    id_2 = BronzeEnvelope.compute_event_id(**kwargs)
    assert id_1 == id_2


def test_event_id_differs_when_payload_differs():
    base_kwargs = {
        "source": "birdeye",
        "token_address": "TokenA",
        "event_timestamp": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    id_1 = BronzeEnvelope.compute_event_id(**base_kwargs, payload={"c": 1.1})
    id_2 = BronzeEnvelope.compute_event_id(**base_kwargs, payload={"c": 1.2})
    assert id_1 != id_2


def test_event_id_differs_when_source_differs():
    """Two vendors reporting an identical candle for the same token/time must
    NOT collide - they're genuinely different observations, not duplicates."""
    kwargs = {
        "token_address": "TokenA",
        "event_timestamp": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        "payload": {"c": 1.1},
    }
    id_birdeye = BronzeEnvelope.compute_event_id(source="birdeye", **kwargs)
    id_other = BronzeEnvelope.compute_event_id(source="other_vendor", **kwargs)
    assert id_birdeye != id_other
