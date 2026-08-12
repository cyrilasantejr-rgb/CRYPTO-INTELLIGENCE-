# Phase 7: Airflow Orchestration - Local Setup

Airflow runs in its own separate virtual environment, deliberately isolated
from the project's main venv (see ADR-014 in decisions.md for why).

## One-time setup

From your project root, with your project venv NOT active (or in a fresh
terminal tab):

```
python3 -m venv airflow_venv
source airflow_venv/bin/activate
pip install --upgrade pip
pip install "apache-airflow==3.3.0" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.0/constraints-3.12.txt"
```

Set the Airflow home directory to somewhere inside the project (keeps
everything self-contained) and point Airflow at our DAGs folder:

```
export AIRFLOW_HOME="$(pwd)/airflow_home"
mkdir -p "$AIRFLOW_HOME"
airflow config get-value core dags_folder  # just to confirm it runs
```

By default Airflow looks for DAGs in `$AIRFLOW_HOME/dags` - point it at
our actual DAG folder instead by adding this to `$AIRFLOW_HOME/airflow.cfg`
under the `[core]` section once it's generated (or set the env var below
before every command):

```
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
```

## Configure the DAG's Variables (one-time)

The DAG reads its project path, Python interpreter, and token from Airflow
Variables rather than hardcoding them - run these once, with your ACTUAL
paths:

```
airflow variables set CRYPTO_PROJECT_ROOT "$(pwd)"
airflow variables set CRYPTO_VENV_PYTHON "$(pwd)/venv/bin/python3"
airflow variables set CRYPTO_TOKEN "So11111111111111111111111111111111111111112"
```

## Start Airflow

```
airflow standalone
```

This initializes the metadata database, creates an admin user (username
`admin`, password printed in the terminal output - look for it near the
top), and starts both the webserver and scheduler in one process. This
mode is officially intended for local development/testing only, not
production.

Open `http://localhost:8080` in your browser, log in with the printed
credentials, find `crypto_intelligence_pipeline` in the DAG list, and
trigger it manually (the play button). Watch the task graph - you should
see `ingest_market_data` run first, then `bronze_to_silver`, then
`silver_to_gold_features`, then `backtest` and `train_models` running
side by side.

## Every time after that

You only need to re-run `airflow standalone` (with `AIRFLOW_HOME` and
`AIRFLOW__CORE__DAGS_FOLDER` still exported) - the Variables and metadata
database persist between runs.
