"""
Phase 14: after running ml.run_training (which logs each run's metrics
to MLflow), this checks the LATEST run for a given token/model_type
against the current champion (from the local registry) and promotes it
if it clears the bar - see promotion_logic.py for the exact criteria
and ADR-035 for why those specific criteria were chosen.

Usage:

    python3 -m model_retraining.run_promotion_check --token <address> --model-type entry
"""

from __future__ import annotations

import argparse
import logging

import mlflow

from model_retraining.champion_registry import load_champion_metrics, save_champion
from model_retraining.promotion_logic import decide_promotion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MLFLOW_TRACKING_URI = "sqlite:///mlflow.db"
MLFLOW_EXPERIMENT_NAME = "crypto-intelligence-models"


def _get_latest_run_metrics(token: str, model_type: str) -> tuple[dict, str] | None:
    """Returns (metrics, run_id) for the most recent MLflow run matching
    this token/model_type, or None if no matching run exists yet."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    experiment = mlflow.get_experiment_by_name(MLFLOW_EXPERIMENT_NAME)
    if experiment is None:
        return None

    runs = mlflow.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"params.token = '{token}' and params.model_type = '{model_type}'",
        order_by=["start_time DESC"],
        max_results=1,
    )

    if runs.empty:
        return None

    latest = runs.iloc[0]
    metric_columns = [c for c in runs.columns if c.startswith("metrics.")]
    metrics = {
        col.removeprefix("metrics."): latest[col]
        for col in metric_columns
        if latest[col] is not None
    }
    return metrics, latest["run_id"]


def run(token: str, model_type: str) -> None:
    result = _get_latest_run_metrics(token, model_type)
    if result is None:
        logger.error(
            "No MLflow runs found for token=%s, model_type=%s. Run "
            "ml.run_training first.",
            token,
            model_type,
        )
        return

    challenger_metrics, run_id = result
    champion_metrics = load_champion_metrics(token, model_type)

    logger.info("Challenger (run %s) metrics: %s", run_id, challenger_metrics)
    logger.info("Current champion metrics: %s", champion_metrics)

    decision = decide_promotion(champion_metrics, challenger_metrics)

    logger.info(
        "=== Promotion decision: %s ===", "PROMOTE" if decision.promote else "REJECT"
    )
    for reason in decision.reasons:
        logger.info("  - %s", reason)

    if decision.promote:
        save_champion(token, model_type, challenger_metrics, mlflow_run_id=run_id)
        logger.info("New champion saved to registry.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Champion/challenger promotion check")
    parser.add_argument("--token", required=True)
    parser.add_argument("--model-type", required=True, choices=["entry", "exit"])
    args = parser.parse_args()
    run(args.token, args.model_type)


if __name__ == "__main__":
    main()
