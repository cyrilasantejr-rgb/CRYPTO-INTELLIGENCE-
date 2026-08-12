#!/bin/bash
#
# Runs the full pipeline (ingest -> Silver -> Gold -> backtest -> training)
# in sequence. Designed to be launched by macOS's launchd, NOT by hand from
# an interactive terminal - which is why every path below is absolute and
# nothing relies on a shell profile (.zshrc/.bash_profile) being loaded.
# launchd jobs run in a minimal environment with none of that.
#
# See docs/daily_automation_setup.md for how to actually schedule this.

set -euo pipefail  # exit immediately on any command failure, undefined var, or pipe failure

# ---- Configuration - edit these three lines for your machine ----
PROJECT_ROOT="/Users/150ril/Documents/CRYPTO-INTELLIGENCE-"
VENV_PYTHON="${PROJECT_ROOT}/venv/bin/python3"
TOKEN="So11111111111111111111111111111111111111112"
# -------------------------------------------------------------------

LOG_DIR="${PROJECT_ROOT}/logs/daily"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/$(date +%Y-%m-%d_%H-%M-%S).log"

# Redirect all output (both stdout and stderr) to the log file AND still
# show it if run interactively - `tee` does both at once.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Daily pipeline run started: $(date) ==="

cd "$PROJECT_ROOT"

echo "--- Ensuring MinIO is running ---"
# Idempotent: docker compose up is safe to run even if the container is
# already up - it just confirms the desired state, doesn't restart it.
docker compose up -d minio

echo "--- Stage 1/5: Historical market ingestion ---"
"$VENV_PYTHON" -m ingestion.market.run_historical_ingestion \
  --token "$TOKEN" --days 7 --interval 1H

echo "--- Stage 2/5: Bronze to Silver ---"
"$VENV_PYTHON" -m databricks.silver.run_market_silver

echo "--- Stage 3/5: Silver to Gold features ---"
"$VENV_PYTHON" -m features.run_market_features

echo "--- Stage 4/5: Backtest ---"
"$VENV_PYTHON" -m backtesting.run_backtest

echo "--- Stage 5/5: Model training ---"
"$VENV_PYTHON" -m ml.run_training

echo "=== Daily pipeline run finished successfully: $(date) ==="
