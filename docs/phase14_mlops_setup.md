# Phase 14 (first slice): MLOps - Experiment Tracking & Champion/Challenger

Wires MLflow experiment tracking into Phase 6's training script, and
implements the champion/challenger promotion logic your original design
explicitly required: never replace a better model with a worse one just
for being newer.

## Running training (now with MLflow tracking)

Same command as before - MLflow tracking happens automatically:

```
source venv/bin/activate
python3 -m ml.run_training
```

This now also creates `mlflow.db` (a local SQLite file - see ADR-036)
recording every run's parameters, metrics, and the trained model itself.

## Viewing experiment history in MLflow's UI

```
mlflow ui --backend-store-uri sqlite:///mlflow.db
```

Then open `http://localhost:5000` in a browser to see every training
run, its metrics, and parameters.

## Checking whether a new model should be promoted

```
python3 -m model_retraining.run_promotion_check --token So11111111111111111111111111111111111111112 --model-type entry
```

Compares the most recent MLflow run for this token/model-type against
the current champion (if any) and promotes it if it clears the bar -
see ADR-035 for the exact criteria (meaningful ROC-AUC improvement,
without meaningfully regressing calibration).

## How promotion works

Two conditions, both required (unless there's no champion yet, in which
case the first model is promoted automatically):
1. ROC-AUC improves by at least 0.02
2. Brier score (calibration) doesn't regress by more than 0.01

A model that improves ROC-AUC by becoming overconfident (worse
calibration) is correctly rejected, not promoted - this was verified
with a dedicated test constructing exactly that scenario.

## Known limitation

This first slice checks promotion manually, on demand - it's not yet
wired into an automatic retraining schedule or drift-detection trigger
(the MODEL DRIFT section of the original design: monitoring feature/
prediction distribution changes over time to decide when retraining is
warranted). That's real future work, not built yet.
