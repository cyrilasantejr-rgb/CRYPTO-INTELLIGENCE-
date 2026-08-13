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
import mlflow
import mlflow.sklearn
import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

from common.storage.object_store import ObjectStoreClient
from ml.entry.labels import triple_barrier_label
from ml.entry.split import chronological_split
from ml.entry.train import prepare_training_data, train_entry_model
from ml.exit.labels import exit_label
from ml.exit.train import (
    add_simulated_position_features,
    prepare_exit_training_data,
    train_exit_model,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Phase 14: MLflow experiment tracking, SQLite-backed - same reasoning
# as Phase 13's position storage (ADR-033): a single local file gives
# full experiment tracking AND model registry functionality without
# needing a separate tracking server, right-sized for this project's
# actual scale. Production alternative, stated plainly: run `mlflow
# server` with a real backend if this ever needs multi-user access.
MLFLOW_TRACKING_URI = "sqlite:///mlflow.db"
MLFLOW_EXPERIMENT_NAME = "crypto-intelligence-models"

GOLD_PREFIX = "gold/token_market_features/"
MODELS_DIR = Path("models")

# Entry model: does price rise before falling, within 6 hours?
# Thresholds tuned against observed data, not picked arbitrarily: a
# real 7-day SOL pull showed a total peak-to-trough range of ~6.7% across
# the ENTIRE week - a 3%-within-6-hours move (the original threshold)
# essentially never occurs at that volatility, which is exactly why the
# first real run of this script produced an all-zero label column and
# crashed sklearn with "only one class present". 1%/1% is still a real,
# meaningful move at this token's actual volatility, not a threshold
# picked to force some 1s to appear.
ENTRY_HORIZON = 6
ENTRY_UPPER_PCT = 0.01
ENTRY_LOWER_PCT = 0.01

# Exit model: given a simulated 4-hour-old position, does price drop
# meaningfully at any point in the next 3 hours? Lowered for the same
# reason as the entry thresholds above - see comment there.
EXIT_HOLDING_PERIOD = 4
EXIT_HORIZON = 3
EXIT_DECLINE_THRESHOLD = 0.01


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


def _log_metrics_safely(metrics: dict) -> None:
    """
    MLflow's log_metrics() rejects None values outright (it requires an
    actual float per metric) - but this project's models legitimately
    return None for roc_auc when a test set has zero positive examples
    (undefined ROC-AUC, not a bug - see ml/entry/train.py). Filtering
    None values out here, with a clear log line about what was skipped
    and why, keeps that already-correct "return None, don't crash"
    behavior intact instead of MLflow's stricter validation crashing
    the whole training run over a value that was always going to be
    unavailable for this particular run.
    """
    skipped = [key for key, value in metrics.items() if value is None]
    if skipped:
        logger.warning(
            "Skipping MLflow logging for metric(s) with undefined value "
            "(None): %s - this is expected when a test set has zero "
            "positive examples, not an error",
            skipped,
        )
    loggable = {key: value for key, value in metrics.items() if value is not None}
    if loggable:
        mlflow.log_metrics(loggable)


def run() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

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
            # Guard against a single-class training set - a real
            # possibility with a short history or low-volatility window
            # (this is exactly what happened on the first real run
            # against 7 days of a stable token: every label came back 0,
            # and sklearn's LogisticRegression.fit() crashed with an
            # unhelpful "only one class present" error). Skip with a
            # clear, actionable log message instead of letting the whole
            # training run die - the same "quarantine and continue"
            # philosophy as Silver's data-quality handling, applied to
            # model training rather than row validation.
            if entry_train["label"].nunique() < 2:
                logger.warning(
                    "[%s] entry training set has only one label class "
                    "(all %s) - skipping. Try a longer history, a more "
                    "volatile token, or looser barrier thresholds.",
                    token,
                    entry_train["label"].iloc[0],
                )
            else:
                with mlflow.start_run(run_name=f"entry_{token}"):
                    entry_result = train_entry_model(entry_train, entry_test)
                    logger.info(
                        "[%s] ENTRY MODEL metrics: %s", token, entry_result.metrics
                    )
                    joblib.dump(
                        entry_result.model, MODELS_DIR / f"entry_{token}.joblib"
                    )
                    joblib.dump(
                        entry_result.scaler, MODELS_DIR / f"entry_scaler_{token}.joblib"
                    )

                    mlflow.log_params(
                        {
                            "token": token,
                            "model_type": "entry",
                            "horizon": ENTRY_HORIZON,
                            "upper_pct": ENTRY_UPPER_PCT,
                            "lower_pct": ENTRY_LOWER_PCT,
                            "train_rows": len(entry_train),
                            "test_rows": len(entry_test),
                        }
                    )
                    _log_metrics_safely(entry_result.metrics)
                    mlflow.sklearn.log_model(entry_result.model, name="model")
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
            # Same class-balance guard as the entry model above.
            if exit_train["exit_label"].nunique() < 2:
                logger.warning(
                    "[%s] exit training set has only one label class "
                    "(all %s) - skipping.",
                    token,
                    exit_train["exit_label"].iloc[0],
                )
            else:
                with mlflow.start_run(run_name=f"exit_{token}"):
                    exit_result = train_exit_model(exit_train, exit_test)
                    logger.info(
                        "[%s] EXIT MODEL metrics: %s", token, exit_result.metrics
                    )
                    joblib.dump(exit_result.model, MODELS_DIR / f"exit_{token}.joblib")
                    joblib.dump(
                        exit_result.scaler, MODELS_DIR / f"exit_scaler_{token}.joblib"
                    )

                    mlflow.log_params(
                        {
                            "token": token,
                            "model_type": "exit",
                            "holding_period": EXIT_HOLDING_PERIOD,
                            "horizon": EXIT_HORIZON,
                            "decline_threshold": EXIT_DECLINE_THRESHOLD,
                            "train_rows": len(exit_train),
                            "test_rows": len(exit_test),
                        }
                    )
                    _log_metrics_safely(exit_result.metrics)
                    mlflow.sklearn.log_model(exit_result.model, name="model")
        else:
            logger.warning(
                "[%s] not enough usable rows (%d) to train exit model - skipping",
                token,
                len(exit_ready),
            )

    logger.info("Models saved locally to %s/", MODELS_DIR)


if __name__ == "__main__":
    run()
