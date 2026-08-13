"""
Local champion registry - a simple JSON file recording the CURRENT
champion model's metrics for each (token, model_type) pair, so a newly
trained challenger's metrics can be compared against it via
promotion_logic.decide_promotion().

Kept deliberately separate from MLflow's own Model Registry concept:
MLflow tracks every run's metrics regardless of promotion status (full
history/lineage, per the project's original MLOps requirements); this
registry tracks only "which one is currently the champion" - a much
narrower, simpler piece of state that a promotion check needs to read
and update quickly, without needing to query MLflow's run history to
figure out which past run is the current champion.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path("models") / "champion_registry.json"


def _registry_key(token: str, model_type: str) -> str:
    return f"{model_type}:{token}"


def load_champion_metrics(
    token: str, model_type: str, registry_path: Path = DEFAULT_REGISTRY_PATH
) -> dict[str, float] | None:
    """Returns the current champion's metrics, or None if no champion
    has been registered yet for this (token, model_type) pair."""
    if not registry_path.exists():
        return None

    with open(registry_path) as f:
        registry = json.load(f)

    entry = registry.get(_registry_key(token, model_type))
    return entry["metrics"] if entry is not None else None


def save_champion(
    token: str,
    model_type: str,
    metrics: dict[str, float],
    mlflow_run_id: str | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> None:
    """Records challenger_metrics as the new champion for this
    (token, model_type) pair - overwrites any previous champion entry."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    registry: dict = {}
    if registry_path.exists():
        with open(registry_path) as f:
            registry = json.load(f)

    registry[_registry_key(token, model_type)] = {
        "metrics": metrics,
        "mlflow_run_id": mlflow_run_id,
    }

    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)
