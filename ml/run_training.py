"""
Runner for Phase 6: train the baseline entry and exit models.

Usage (run locally, with MinIO running via `docker compose up -d minio`):

    python3 -m ml.run_training

Downloads gold/token_market_features/ (same loader as Phase 5's
backtester - see ADR-011), generates labels, trains both models with a
strictly chronological split, prints full evaluation metrics for each,
and saves both models locally under models/ via joblib.

HONEST CAVEAT, worth repeating here where it'll actually be seen at run
time: with one token and one week of hourly data (~168 rows before any
nulls are dropped), these metrics are not trustworthy evidence of a
working trading edge - they're a check that the PIPELINE (labels,
chronological split, training, evaluation) is correct. More data and
more tokens are what would make the resulting numbers meaningful.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import joblib
import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

from common.storage.object_store import ObjectStoreClient
from ml.entry.labels import triple_barrier_label
from ml.entry.split import chronological_split
from ml.entry.train import has_both_classes, prepare_training_data, train_entry_model
from ml.exit.labels import exit_label
from ml.exit.train import (
    add_simulated_position_features,
    prepare_exit_training_data,
    train_exit_model,
)
from ml.exit.train import (
    has_both_classes as exit_has_both_classes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GOLD_PREFIX = "gold/token_market_features/"
MODELS_DIR = Path("models")

# Entry model: does price rise 1.5% before falling 1.5%, within 12 hours?
# (Loosened from an initial 3%/2%/6h - a real run against one quiet
# week of SOL data had zero candles hitting a 3% move that fast, which
# is a legitimate market observation, not a bug. These are tunable
# hyperparameters, not fixed truth - revisit once more data/tokens exist.)
ENTRY_HORIZON = 12
ENTRY_UPPER_PCT = 0.015
ENTRY_LOWER_PCT = 0.015

# Exit model: given a simulated 4-hour-old position, does price drop
# more than 1.5% at any point in the next 6 hours?
EXIT_HOLDING_PERIOD = 4
EXIT_HORIZON = 6
EXIT_DECLINE_THRESHOLD = 0.015


def _make_store() -> ObjectStoreClient:
    load_dotenv()
    return ObjectStoreClient(
        bucket=os.environ.get("S3_BUCKET_NAME", "crypto-intelligence"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_SECRET_KEY"),
    )


def _load_gold_as_pandas(store: ObjectStoreClient) -> pd.DataFrame:
    """Same manual Hive-partition-parsing approach as Phase 5's
    backtester - see backtesting/run_backtest.py and ADR-010."""
    keys = store.list_keys(prefix=GOLD_PREFIX)
    if not keys:
        raise RuntimeError(f"No Gold objects found under {GOLD_PREFIX}")

    frames = []
    for key in keys:
        relative = key.removeprefix(GOLD_PREFIX)
        parts = dict(
            segment.split("=", 1)
            for segment in relative.split("/")[:-1]
            if "=" in segment
        )
        table = pq.read_table(io.BytesIO(store.get_object_bytes(key)))
        chunk = table.to_pandas()
        for col, value in parts.items():
            chunk[col] = value
        frames.append(chunk)

    return pd.concat(frames, ignore_index=True)


def run() -> None:
    store = _make_store()
    gold_df = _load_gold_as_pandas(store)
    logger.info("Loaded %d Gold row(s)", len(gold_df))

    MODELS_DIR.mkdir(exist_ok=True)

    for token, group in gold_df.groupby("token_address"):
        group = group.sort_values("event_timestamp").reset_index(drop=True)

        # --- Entry model ---
        group["label"] = triple_barrier_label(
            group,
            horizon=ENTRY_HORIZON,
            upper_pct=ENTRY_UPPER_PCT,
            lower_pct=ENTRY_LOWER_PCT,
        )
        entry_ready = prepare_training_data(group)
        logger.info(
            "[%s] entry: %d rows usable after dropping nulls", token, len(entry_ready)
        )

        if len(entry_ready) >= 20:
            entry_train, entry_test = chronological_split(entry_ready)
            if not has_both_classes(entry_train["label"].to_numpy()):
                logger.warning(
                    "[%s] entry training split has only one label class present "
                    "(likely no candle hit the upper barrier this window) - "
                    "skipping. Try a smaller ENTRY_UPPER_PCT/ENTRY_LOWER_PCT or "
                    "a longer ENTRY_HORIZON for this token.",
                    token,
                )
            else:
                entry_result = train_entry_model(entry_train, entry_test)
                logger.info("[%s] ENTRY MODEL metrics: %s", token, entry_result.metrics)
                joblib.dump(entry_result.model, MODELS_DIR / f"entry_{token}.joblib")
                joblib.dump(
                    entry_result.scaler, MODELS_DIR / f"entry_scaler_{token}.joblib"
                )
        else:
            logger.warning(
                "[%s] not enough usable rows (%d) to train entry model - skipping",
                token,
                len(entry_ready),
            )

        # --- Exit model ---
        group = add_simulated_position_features(
            group, holding_period=EXIT_HOLDING_PERIOD
        )
        group["exit_label"] = exit_label(
            group, horizon=EXIT_HORIZON, decline_threshold=EXIT_DECLINE_THRESHOLD
        )
        exit_ready = prepare_exit_training_data(group)
        logger.info(
            "[%s] exit: %d rows usable after dropping nulls", token, len(exit_ready)
        )

        if len(exit_ready) >= 20:
            exit_train, exit_test = chronological_split(exit_ready)
            if not exit_has_both_classes(exit_train["exit_label"].to_numpy()):
                logger.warning(
                    "[%s] exit training split has only one label class present - "
                    "skipping. Try a smaller EXIT_DECLINE_THRESHOLD or a longer "
                    "EXIT_HORIZON for this token.",
                    token,
                )
            else:
                exit_result = train_exit_model(exit_train, exit_test)
                logger.info("[%s] EXIT MODEL metrics: %s", token, exit_result.metrics)
                joblib.dump(exit_result.model, MODELS_DIR / f"exit_{token}.joblib")
                joblib.dump(
                    exit_result.scaler, MODELS_DIR / f"exit_scaler_{token}.joblib"
                )
        else:
            logger.warning(
                "[%s] not enough usable rows (%d) to train exit model - skipping",
                token,
                len(exit_ready),
            )

    logger.info("Models saved locally to %s/", MODELS_DIR)


if __name__ == "__main__":
    run()
