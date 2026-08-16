"""
Bronze event envelope shared by every ingestion domain.

Every raw event written to the Bronze layer, regardless of source or domain,
is wrapped in this envelope. See docs/architecture.md and docs/data_dictionary.md
for the full design rationale, and ADR-004 in docs/decisions.md for why
event_id is a computed hash rather than a vendor-supplied ID.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Domain = Literal[
    "market", "transaction", "wallet", "holder", "security", "news", "social", "discovery"
]


class BronzeEnvelope(BaseModel):
    event_id: str = Field(
        description="Deterministic dedup key: sha256(source|token_address|"
        "event_timestamp|payload_hash). See ADR-004."
    )
    source: str = Field(description="Vendor name, e.g. 'birdeye', 'helius'")
    schema_version: str = "1.0"
    ingestion_timestamp: datetime = Field(
        description="When OUR system received this event (UTC)"
    )
    event_timestamp: datetime = Field(
        description="When this event actually happened upstream (UTC)"
    )
    token_address: str = Field(description="Solana mint address; Kafka partition key")
    domain: Domain
    payload: dict[str, Any] = Field(description="Raw, unmodified vendor payload")

    @staticmethod
    def compute_event_id(
        source: str,
        token_address: str,
        event_timestamp: datetime,
        payload: dict[str, Any],
    ) -> str:
        """
        Deterministic dedup key, independent of any vendor-provided ID.

        Why: not every vendor gives a stable event ID, and webhook/API
        redelivery of the same event is common. Hashing the semantic
        identity of the event (who + what token + when + exact content)
        means two identical events - however many times they're delivered -
        always produce the same event_id, so Silver-layer dedup is a plain
        "drop duplicate event_id" operation, no vendor-specific logic needed.
        """
        payload_bytes = json.dumps(payload, sort_keys=True, default=str).encode()
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        raw = f"{source}|{token_address}|{event_timestamp.isoformat()}|{payload_hash}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        source: str,
        token_address: str,
        event_timestamp: datetime,
        domain: Domain,
        payload: dict[str, Any],
        schema_version: str = "1.0",
    ) -> BronzeEnvelope:
        """Factory: computes event_id and stamps ingestion_timestamp automatically."""
        event_id = cls.compute_event_id(source, token_address, event_timestamp, payload)
        return cls(
            event_id=event_id,
            source=source,
            schema_version=schema_version,
            ingestion_timestamp=datetime.now(timezone.utc),
            event_timestamp=event_timestamp,
            token_address=token_address,
            domain=domain,
            payload=payload,
        )
