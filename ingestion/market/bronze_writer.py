"""
Writes a batch of BronzeEnvelope objects to the S3/MinIO Bronze layer.

Partitioning scheme:
    bronze/{domain}/dt={event_date}/token={token_address}/{ingestion_run_id}.parquet

Why partition by event_date (not ingestion_date): queries almost always ask
"give me candles for token X during date range D1-D2" - that's a question
about when the event HAPPENED, not when we happened to ingest it. Partitioning
on event_date means a query engine (Spark, Athena) can skip every file outside
the requested date range without opening them - this is "partition pruning",
and it's the single biggest lever for keeping query costs/time down as data
volume grows.

Why the key includes a run id instead of overwriting a fixed key: Bronze is
defined as append-only (docs/architecture.md). If two ingestion runs both
cover 2026-08-01 for the same token (e.g. a daily backfill re-running,
or a manual re-fetch after finding a gap), we want BOTH files to persist
rather than the second silently replacing the first. Silver-layer dedup
(keyed on event_id, see common/schemas/envelope.py) is what reconciles
duplicates across files - this writer's job is only to never lose data.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from common.schemas.envelope import BronzeEnvelope
from common.storage.object_store import ObjectStoreClient

logger = logging.getLogger(__name__)


def write_bronze_batch(
    envelopes: list[BronzeEnvelope], store: ObjectStoreClient
) -> list[str]:
    """
    Groups envelopes by (domain, event_date, token_address) and writes one
    Parquet file per group, using a fresh run id so writes never collide with
    a prior run's file for the same partition.

    Returns the list of s3:// URIs written.
    """
    if not envelopes:
        logger.info("write_bronze_batch called with no envelopes - nothing to write")
        return []

    groups: dict[tuple[str, str, str], list[BronzeEnvelope]] = defaultdict(list)
    for env in envelopes:
        event_date = env.event_timestamp.date().isoformat()
        groups[(env.domain, event_date, env.token_address)].append(env)

    run_id = uuid.uuid4().hex
    written_uris = []

    for (domain, event_date, token_address), group in groups.items():
        key = (
            f"bronze/{domain}/dt={event_date}/"
            f"token={token_address}/{run_id}.parquet"
        )
        records = [env.model_dump(mode="json") for env in group]
        uri = store.write_parquet(records, key)
        written_uris.append(uri)

    logger.info(
        "Wrote %d bronze partition file(s) from %d envelope(s), run_id=%s",
        len(written_uris),
        len(envelopes),
        run_id,
    )
    return written_uris
