"""
Automates the token discovery engine - runs discovery/run_candidate_fetch.py
on a schedule instead of requiring a manual CLI invocation every time.

Same environment-separation pattern as crypto_pipeline_dag.py (see that
file's docstring for the full reasoning): this DAG runs inside Airflow's
own Python environment and only shells out via BashOperator to the
PROJECT venv's python interpreter. Airflow itself never imports
discovery/ directly.

CONFIGURATION - reuses the SAME Airflow Variables as crypto_pipeline_dag.py
(CRYPTO_PROJECT_ROOT, CRYPTO_VENV_PYTHON) since both DAGs run the same
project from the same machine. No new variables needed.

SCHEDULE - @hourly, not @daily like the market pipeline. Discovery's
entire value is catching early momentum before it is already trending;
a token could rise and fall within hours, so a daily cadence would
frequently miss the window.

This is a deliberate tradeoff, not a free choice: bronze_writer.py
currently writes ~100 separate small Parquet files per discovery run
(one per candidate - the "small-files problem" flagged when discovery
persistence was built). Hourly means roughly 100 tiny S3 objects x 24
runs/day = ~2,400 tiny writes/day from this DAG alone. Choosing hourly
here was a deliberate call to prioritize catching early signal over
storage efficiency - but it also means fixing that small-files problem
is no longer just cleanup, it is now the single most urgent piece of
remaining technical debt in this project, created directly by this
scheduling decision.

FILTER PARAMETERS - --limit 100 explicitly: Birdeye's screener costs
75 CU per request regardless of how many results are requested, so
there is no cost reason to ask for fewer. See DiscoveryFilters' own
defaults for the rest of the filter behavior (max_liquidity ceiling,
volume/holder/trade-count floors; wash-trading quarantine happens
downstream, on read, not here).
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

default_args = {
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=2),
}

with DAG(
    dag_id="crypto_discovery_pipeline",
    description="Hourly token discovery screening -> Bronze persistence",
    schedule="@hourly",
    start_date=pendulum.datetime(2026, 8, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["crypto-intelligence", "discovery"],
) as dag:
    discover_candidates = BashOperator(
        task_id="discover_candidates",
        bash_command=f"{VENV_PYTHON} -m discovery.run_candidate_fetch --limit 100",
        cwd=PROJECT_ROOT,
    )
