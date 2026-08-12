# Phase 7: Airflow Orchestration - Local Setup

Airflow runs in its own separate virtual environment, deliberately isolated
from the project's main venv (see ADR-014 in decisions.md for why).

**Using Airflow 2.11.0, not 3.x** - see ADR-015 for why: Airflow 3's newer
Task Execution API caused a concrete, verified failure (`ModuleNotFoundError:
No module named 'airflow.sdk'`) when actually running a task, even though
the DAG parsed and displayed correctly in the UI. 2.11.0 uses the classic,
much simpler DAG API and avoids that failure entirely - proven in isolation
before this fix was written up.

## If you already set up Airflow 3.x from an earlier attempt

Mixing metadata database schemas between Airflow major versions is messy
and not worth troubleshooting - do a clean reinstall instead:

```
deactivate
rm -rf airflow_venv airflow_home
```

Then follow the setup below from scratch.

## One-time setup

From your project root, with your project venv NOT active (or in a fresh
terminal tab):

```
python3 -m venv airflow_venv
source airflow_venv/bin/activate
pip install --upgrade pip
pip install "apache-airflow==2.11.0" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.11.0/constraints-3.12.txt"
```

Set the Airflow home directory to somewhere inside the project (keeps
everything self-contained) and point Airflow at our DAGs folder:

```
export AIRFLOW_HOME="$(pwd)/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
mkdir -p "$AIRFLOW_HOME"
```

## Configure the DAG's Variables (one-time)

The DAG reads its project path, Python interpreter, and token from Airflow
Variables rather than hardcoding them - run these once, with your ACTUAL
paths (this also initializes Airflow's metadata database the first time,
so expect some one-time setup output):

```
airflow variables set CRYPTO_PROJECT_ROOT "$(pwd)"
airflow variables set CRYPTO_VENV_PYTHON "$(pwd)/venv/bin/python3"
airflow variables set CRYPTO_TOKEN "So11111111111111111111111111111111111111112"
```

## Start Airflow

```
airflow standalone
```

This creates an admin user (username `admin`, password printed in the
terminal output - look for a line containing `Password for user 'admin'`
near the top of the output) and starts both the webserver and scheduler.
This mode is officially intended for local development/testing only, not
production. It keeps running and logging continuously - that's normal,
not a sign of being stuck.

Open `http://localhost:8080` in a new terminal tab/browser window (leave
the `airflow standalone` terminal running), log in with the printed
credentials, find `crypto_intelligence_pipeline`, unpause it with the
toggle switch, click into it, and click "Trigger" to run it manually.

Watch the task graph: `ingest_market_data` runs first, then
`bronze_to_silver`, then `silver_to_gold_features`, then `backtest` and
`train_models` running side by side.

## Every time after that

You only need to re-run `airflow standalone` (with `AIRFLOW_HOME` and
`AIRFLOW__CORE__DAGS_FOLDER` still exported) - the Variables and metadata
database persist between runs.
