import tempfile
from pathlib import Path

from model_retraining.champion_registry import load_champion_metrics, save_champion


def test_load_returns_none_when_no_registry_file_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "champion_registry.json"
        result = load_champion_metrics("TokenA", "entry", registry_path=registry_path)
        assert result is None


def test_save_then_load_round_trips_correctly():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "champion_registry.json"
        metrics = {"roc_auc": 0.62, "brier_score": 0.18, "precision": 0.5}

        save_champion("TokenA", "entry", metrics, registry_path=registry_path)
        loaded = load_champion_metrics("TokenA", "entry", registry_path=registry_path)

        assert loaded == metrics


def test_different_token_model_type_pairs_are_independent():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "champion_registry.json"

        save_champion("TokenA", "entry", {"roc_auc": 0.6}, registry_path=registry_path)
        save_champion("TokenA", "exit", {"roc_auc": 0.7}, registry_path=registry_path)
        save_champion("TokenB", "entry", {"roc_auc": 0.8}, registry_path=registry_path)

        assert load_champion_metrics(
            "TokenA", "entry", registry_path=registry_path
        ) == {"roc_auc": 0.6}
        assert load_champion_metrics("TokenA", "exit", registry_path=registry_path) == {
            "roc_auc": 0.7
        }
        assert load_champion_metrics(
            "TokenB", "entry", registry_path=registry_path
        ) == {"roc_auc": 0.8}


def test_saving_a_new_champion_overwrites_the_previous_one():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "champion_registry.json"

        save_champion("TokenA", "entry", {"roc_auc": 0.5}, registry_path=registry_path)
        save_champion("TokenA", "entry", {"roc_auc": 0.7}, registry_path=registry_path)

        loaded = load_champion_metrics("TokenA", "entry", registry_path=registry_path)
        assert loaded == {"roc_auc": 0.7}  # only the latest, not both


def test_saving_one_pair_does_not_disturb_others_already_in_the_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "champion_registry.json"

        save_champion("TokenA", "entry", {"roc_auc": 0.5}, registry_path=registry_path)
        save_champion("TokenB", "entry", {"roc_auc": 0.6}, registry_path=registry_path)
        # Re-save TokenA - must not wipe out TokenB's entry in the same file
        save_champion("TokenA", "entry", {"roc_auc": 0.55}, registry_path=registry_path)

        assert load_champion_metrics(
            "TokenB", "entry", registry_path=registry_path
        ) == {"roc_auc": 0.6}
