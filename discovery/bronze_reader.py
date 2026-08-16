"""
Reads discovery candidates back from the Bronze layer - the read-side
counterpart to run_candidate_fetch.py's writes via write_bronze_batch().

Deliberately plain Python + ObjectStoreClient.read_parquet(), not Spark:
same reasoning as candidate_quality.py - discovery data volume never
justifies Spark. This is what the dashboard uses to show discovered
candidates without making a live Birdeye screener call on every page
render (a screener call costs 75 CU and returns up to 100 NEW addresses
we do not already know - re-running it live per render would be slow
and wasteful compared to reading what a periodic discovery run already
persisted).

NOTE: this reads whatever the MOST RECENT dt= partition with data is,
not literally "today" - discovery runs are currently manual (no
automation wired up yet), so "today" could easily have zero runs.
Reading the latest available date avoids an empty result just because
nobody has run discovery yet today.
"""

from __future__ import annotations

import logging

from common.schemas.envelope import BronzeEnvelope
from common.storage.object_store import ObjectStoreClient
from discovery.candidate_quality import validate_candidates

logger = logging.getLogger(__name__)

DISCOVERY_PREFIX = "bronze/discovery/"


def _latest_partition_date(store: ObjectStoreClient) -> str | None:
    """
    Scans all discovery Bronze keys and returns the most recent
    dt=YYYY-MM-DD value present, or None if nothing has been written yet.

    Key shape: bronze/discovery/dt=2026-08-16/token=.../{run_id}.parquet
    """
    keys = store.list_keys(DISCOVERY_PREFIX)
    dates = set()
    for key in keys:
        for part in key.split("/"):
            if part.startswith("dt="):
                dates.add(part.removeprefix("dt="))
    if not dates:
        return None
    return max(dates)


def read_latest_valid_candidates(store: ObjectStoreClient) -> list[BronzeEnvelope]:
    """
    Reads every discovery candidate written under the most recent dt=
    partition, re-applies validate_candidates() (Bronze itself never
    filtered on write - see run_candidate_fetch.py), and returns only
    the VALID ones. Quarantined candidates were persisted for audit
    purposes but should never surface as something a human would act on.
    """
    latest_date = _latest_partition_date(store)
    if latest_date is None:
        logger.info("No discovery Bronze data found under %s", DISCOVERY_PREFIX)
        return []

    prefix = f"{DISCOVERY_PREFIX}dt={latest_date}/"
    keys = store.list_keys(prefix)

    envelopes: list[BronzeEnvelope] = []
    for key in keys:
        for record in store.read_parquet(key):
            envelopes.append(BronzeEnvelope.model_validate(record))

    valid, quarantined = validate_candidates(envelopes)
    logger.info(
        "Read %d discovery candidate(s) from dt=%s: %d valid, %d quarantined",
        len(envelopes),
        latest_date,
        len(valid),
        len(quarantined),
    )
    return valid
