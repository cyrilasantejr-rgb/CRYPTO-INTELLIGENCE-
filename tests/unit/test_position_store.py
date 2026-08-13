import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paper_trading.position_math import open_position, take_partial_profit
from paper_trading.position_store import PositionStore

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    """A real, temporary SQLite file per test - not mocked, so this
    actually exercises real persistence round-trips, not assumptions
    about how sqlite3 behaves."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_positions.db"
        yield PositionStore(db_path=db_path)


def test_save_new_position_returns_an_id(store):
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    position_id = store.save(pos)
    assert isinstance(position_id, int)
    assert position_id > 0


def test_saved_position_round_trips_correctly(store):
    """The core correctness property: everything about a Position must
    survive a save-then-load cycle unchanged, including nested list
    data (profit_taking_history) that requires JSON serialization."""
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.2, sell_price=15.0, timestamp=NOW)

    position_id = store.save(pos)
    loaded = store.get(position_id)

    assert loaded.token_address == pos.token_address
    assert loaded.entry_price == pos.entry_price
    assert loaded.entry_timestamp == pos.entry_timestamp
    assert loaded.remaining_size == pytest.approx(pos.remaining_size)
    assert loaded.realized_pnl == pytest.approx(pos.realized_pnl)
    assert loaded.status == pos.status
    assert len(loaded.profit_taking_history) == 1
    assert loaded.profit_taking_history[0]["sell_fraction"] == 0.2


def test_updating_an_existing_position_does_not_create_a_duplicate_row(store):
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    position_id = store.save(pos)

    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=15.0, timestamp=NOW)
    same_id = store.save(pos, position_id=position_id)

    assert same_id == position_id
    all_positions = store.list_all_positions()
    assert len(all_positions) == 1  # still just one row, updated in place

    loaded = store.get(position_id)
    assert loaded.remaining_size == pytest.approx(50.0)


def test_list_open_positions_excludes_closed_ones(store):
    open_pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    store.save(open_pos)

    closed_pos = open_position("TokenB", entry_price=5.0, size=50, entry_timestamp=NOW)
    closed_pos = take_partial_profit(
        closed_pos, sell_fraction=1.0, sell_price=8.0, timestamp=NOW
    )
    store.save(closed_pos)

    open_only = store.list_open_positions()
    assert len(open_only) == 1
    assert open_only[0][1].token_address == "TokenA"

    all_positions = store.list_all_positions()
    assert len(all_positions) == 2


def test_get_nonexistent_position_returns_none_not_crash(store):
    assert store.get(9999) is None


def test_multiple_positions_for_different_tokens_are_independent(store):
    pos_a = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos_b = open_position("TokenB", entry_price=20.0, size=50, entry_timestamp=NOW)

    id_a = store.save(pos_a)
    id_b = store.save(pos_b)

    loaded_a = store.get(id_a)
    loaded_b = store.get(id_b)

    assert loaded_a.token_address == "TokenA"
    assert loaded_b.token_address == "TokenB"
    assert loaded_a.entry_price != loaded_b.entry_price
