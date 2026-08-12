# Phase 7: Airflow Orchestration - Local Setup

Airflow runs in its own separate virtual environment, deliberately isolated
from the project's main venv (see ADR-014 in decisions.md for why).

**Using Airflow 2.11.0, not 3.x** - see ADR-015 for why: Airflow 3's newer
Task Execution API caused a concrete, verified failure
(`ModuleNotFoundError: No module named 'airflow.sdk'`) when actually
running a task, even though the DAG parsed and displayed correctly in the
UI. 2.11.0 uses the classic, much simpler DAG API and avoids that failure
entirely.

## Known limitation on this setup

If the scheduler dispatches a task and it hangs indefinitely at high CPU
usage (check with `ps aux | grep airflow`), this is a known, isolated
issue with this specific macOS + SQLite/SequentialExecutor combination -
see ADR-017 for the full diagnosis. It is NOT a bug in this project's
pipeline code: `airflow tasks test <dag_id> <task_id> <date>` runs any
task directly (bypassing the affected code path) and can be used to
validate that every task's logic is correct, which it is. If you want to
actually get the scheduler's automatic dispatch working, the next thing
to try (not yet attempted) is switching to a Postgres metadata backend so
Airflow can use `LocalExecutor` instead of `SequentialExecutor`.

## macOS-specific environment variables (required, every terminal tab)

Two separate, well-documented macOS + Python issues affect Airflow's
`fork()`-based process launching on this platform. Both are real, verified
issues hit and fixed in this project - see ADR-015 and ADR-016:

```
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export AIRFLOW__CORE__EXECUTE_TASKS_NEW_PYTHON_INTERPRETER=True
```

The first prevents gunicorn's webserver workers from crashing with
`SIGSEGV` on fork (macOS's Objective-C runtime is not fork-safe). The
second is Airflow's own official setting to avoid `fork()` entirely when
launching each task's supervisor process, spawning a fresh subprocess
instead - this fixes a *different* macOS failure mode where the task
supervisor spins at 100% CPU indefinitely instead of completing, tied to
broken proxy-detection threading behavior after fork on macOS (documented
across multiple Airflow GitHub discussions - this is a known, common
issue, not specific to this machine).

Export both in **every** terminal tab before running any `airflow`
command - scheduler, webserver, and CLI commands all need them
independently, since each is its own process with its own environment.

## If you already set up an earlier, broken attempt

Mixing metadata database state between attempts is messy and not worth
troubleshooting - clean reinstall instead:

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

Set the Airflow home directory to somewhere inside the project and point
Airflow at our DAGs folder:

```
export AIRFLOW_HOME="$(pwd)/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export AIRFLOW__CORE__EXECUTE_TASKS_NEW_PYTHON_INTERPRETER=True
mkdir -p "$AIRFLOW_HOME"
```

## Configure the DAG's Variables (one-time)

Run these once, with your ACTUAL paths (this also initializes Airflow's
metadata database the first time, so expect some one-time setup output -
if a "confirm database initialize" prompt times out before you can answer
`y`, just re-run the same command, it goes through instantly once the
database exists):

```
airflow variables set CRYPTO_PROJECT_ROOT "$(pwd)"
airflow variables set CRYPTO_VENV_PYTHON "$(pwd)/venv/bin/python3"
airflow variables set CRYPTO_TOKEN "So11111111111111111111111111111111111111112"
airflow variables list
```

Confirm all three show up in that last command before moving on.

## Running it: scheduler and webserver as separate processes

Rather than `airflow standalone` (which bundles both into one gunicorn-
based process and has proven less reliable on macOS in practice), run
them separately - this also mirrors how real Airflow deployments actually
work, with the scheduler and webserver as independent services.

**Terminal tab 1 - the scheduler (does the actual work, required):**

```
cd ~/Documents/CRYPTO-INTELLIGENCE-
source airflow_venv/bin/activate
export AIRFLOW_HOME="$(pwd)/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export AIRFLOW__CORE__EXECUTE_TASKS_NEW_PYTHON_INTERPRETER=True
airflow scheduler
```

Leave this running. You'll see continuous log output (including some
harmless `RemovedInAirflow3Warning` deprecation noise) - that's normal.

**Terminal tab 2 - trigger and monitor via CLI (reliable, no UI needed):**

```
cd ~/Documents/CRYPTO-INTELLIGENCE-
source airflow_venv/bin/activate
export AIRFLOW_HOME="$(pwd)/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export AIRFLOW__CORE__EXECUTE_TASKS_NEW_PYTHON_INTERPRETER=True
airflow dags unpause crypto_intelligence_pipeline
airflow dags trigger crypto_intelligence_pipeline
```

Check progress periodically with (replace the run id with whatever
`trigger` printed):

```
airflow tasks states-for-dag-run crypto_intelligence_pipeline <run_id>
```

You should see each task move `None` -> `queued` -> `running` ->
`success`, one at a time following the dependency chain, with `backtest`
and `train_models` running side by side near the end.

**Terminal tab 3 - the web UI (optional, nice-to-have):**

```
cd ~/Documents/CRYPTO-INTELLIGENCE-
source airflow_venv/bin/activate
export AIRFLOW_HOME="$(pwd)/airflow_home"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
airflow webserver --port 8080
```

Open `http://localhost:8080`. If this crashes with a `SIGSEGV` loop even
with the fork-safety variable set, that's a known, harder-to-pin-down
gunicorn/macOS interaction - it's fine to skip the web UI entirely and
rely on the CLI commands above, which are fully reliable and don't depend
on gunicorn at all.

## Every time after that

Re-export all four environment variables in each tab you use (they don't
persist between terminal sessions) before running `airflow scheduler` or
any `airflow` CLI command. The Variables and metadata database persist
between runs, so you won't need to set those again.
