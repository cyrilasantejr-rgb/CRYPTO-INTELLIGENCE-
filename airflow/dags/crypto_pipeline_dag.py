"""
Phase 7: orchestrates the full pipeline (historical ingestion -> Bronze ->
Silver -> Gold features -> backtest + training) as a single Airflow DAG.

ENVIRONMENT SEPARATION - read this before touching anything below.

This DAG runs inside Airflow's OWN Python environment (a separate venv from
the project's main one - see docs/decisions.md ADR-014 for why). Every task
below is a BashOperator that shells out to the PROJECT venv's python
interpreter to actually run our pipeline code. Airflow itself never imports
ingestion/, databricks/, features/, backtesting/, or ml/ directly - it only
knows how to launch and monitor external commands. This keeps Airflow's own
heavy, version-pinned dependencies from ever touching (and potentially
breaking) the pipeline code's dependencies, and mirrors how a real deployment
would separate an orchestrator from its worker/execution environments.

CONFIGURATION - set once via the Airflow CLI before running this DAG:

    airflow variables set CRYPTO_PROJECT_ROOT /absolute/path/to/CRYPTO-INTELLIGENCE-
    airflow variables set CRYPTO_VENV_PYTHON /absolute/path/to/CRYPTO-INTELLIGENCE-/venv/bin/python3
    airflow variables set CRYPTO_TOKEN So11111111111111111111111111111111111111112

These are read at DAG-parse time via Variable.get() with defaults, so the
DAG file itself never hardcodes a path specific to any one machine.

WATCHLIST SCOPE - stated plainly, not hidden: this DAG hardcodes a single
token via CRYPTO_TOKEN rather than dynamically generating one task per
token in a watchlist. That's a deliberate scope decision for Phase 7 - see
ADR-014 for the tradeoff. A real watchlist-driven version would use
Airflow's dynamic task mapping (.expand()) to generate one ingestion task
per token from a list pulled from config or a database.
"""

from __future__ import annotations

import pendulum
from airflow.models import Variable
from airflow.operators.bash import BashOperator

from airflow import DAG

PROJECT_ROOT = Variable.get("CRYPTO_PROJECT_ROOT", default_var="/CHANGE_ME")
VENV_PYTHON = Variable.get(
    "CRYPTO_VENV_PYTHON", default_var="/CHANGE_ME/venv/bin/python3"
)
TOKEN = Variable.get(
    "CRYPTO_TOKEN", default_var="So11111111111111111111111111111111111111112"
)

default_args = {
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=2),
}

with DAG(
    dag_id="crypto_intelligence_pipeline",
    description="Ingestion -> Bronze -> Silver -> Gold -> backtest + training",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 8, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["crypto-intelligence"],
) as dag:
    ingest = BashOperator(
        task_id="ingest_market_data",
        bash_command=(
            f"{VENV_PYTHON} -m ingestion.market.run_historical_ingestion "
            f"--token {TOKEN} --days 7 --interval 1H"
        ),
        cwd=PROJECT_ROOT,
    )

    bronze_to_silver = BashOperator(
        task_id="bronze_to_silver",
        bash_command=f"{VENV_PYTHON} -m databricks.silver.run_market_silver",
        cwd=PROJECT_ROOT,
    )

    silver_to_gold_features = BashOperator(
        task_id="silver_to_gold_features",
        bash_command=f"{VENV_PYTHON} -m features.run_market_features",
        cwd=PROJECT_ROOT,
    )

    # Backtest and training both only depend on Gold features, not on each
    # other - they run in parallel, which Airflow's scheduler figures out
    # automatically from the dependency graph below.
    backtest = BashOperator(
        task_id="backtest",
        bash_command=f"{VENV_PYTHON} -m backtesting.run_backtest",
        cwd=PROJECT_ROOT,
    )

    train_models = BashOperator(
        task_id="train_models",
        bash_command=f"{VENV_PYTHON} -m ml.run_training",
        cwd=PROJECT_ROOT,
    )

    ingest >> bronze_to_silver >> silver_to_gold_features >> [backtest, train_models]
