from datetime import datetime, timezone

import pytest

from paper_trading.position_math import (
    open_position,
    position_value,
    take_partial_profit,
    unrealized_pnl,
    unrealized_pnl_pct,
    update_highest_price,
)

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_open_position_rejects_non_positive_price():
    with pytest.raises(ValueError):
        open_position("TokenA", entry_price=0.0, size=100, entry_timestamp=NOW)


def test_open_position_rejects_non_positive_size():
    with pytest.raises(ValueError):
        open_position("TokenA", entry_price=1.0, size=-5, entry_timestamp=NOW)


def test_open_position_sets_highest_price_to_entry_price_initially():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    assert pos.highest_price_since_entry == 10.0
    assert pos.status == "OPEN"
    assert pos.realized_pnl == 0.0


def test_unrealized_pnl_positive_when_price_rises():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    assert unrealized_pnl(pos, current_price=15.0) == 500.0  # 100 * (15-10)


def test_unrealized_pnl_negative_when_price_falls():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    assert unrealized_pnl(pos, current_price=8.0) == -200.0  # 100 * (8-10)


def test_unrealized_pnl_pct_independent_of_size():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    assert unrealized_pnl_pct(pos, current_price=12.0) == pytest.approx(0.20)


def test_position_value_reflects_current_price():
    pos = open_position("TokenA", entry_price=10.0, size=50, entry_timestamp=NOW)
    assert position_value(pos, current_price=20.0) == 1000.0


def test_update_highest_price_only_moves_up():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = update_highest_price(pos, current_price=15.0)
    assert pos.highest_price_since_entry == 15.0

    # A lower price afterward must NOT lower the recorded high -
    # highest_price_since_entry tracks the peak, not the latest price.
    pos = update_highest_price(pos, current_price=12.0)
    assert pos.highest_price_since_entry == 15.0


def test_update_highest_price_does_not_mutate_the_input():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    updated = update_highest_price(pos, current_price=15.0)
    assert pos.highest_price_since_entry == 10.0  # original untouched
    assert updated.highest_price_since_entry == 15.0


def test_partial_sell_reduces_remaining_size_correctly():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.2, sell_price=15.0, timestamp=NOW)

    assert pos.remaining_size == pytest.approx(80.0)  # sold 20 of 100
    assert pos.status == "OPEN"  # still holding 80%


def test_partial_sell_realized_pnl_is_computed_correctly():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.2, sell_price=15.0, timestamp=NOW)

    # Sold 20 tokens at (15 - 10) profit each = 100
    assert pos.realized_pnl == pytest.approx(100.0)


def test_second_partial_sell_is_relative_to_remaining_not_original():
    """The core documented semantic: 'sell 20%' after an earlier sell
    means 20% of what's CURRENTLY held, not 20% of the original size -
    this test would fail if that interpretation were ever silently
    changed."""
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=15.0, timestamp=NOW)
    assert pos.remaining_size == pytest.approx(50.0)

    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=20.0, timestamp=NOW)
    # 50% of the remaining 50 = 25, leaving 25 - NOT 50% of the
    # original 100 (which would leave 0 or go negative on a 3rd sell).
    assert pos.remaining_size == pytest.approx(25.0)


def test_realized_pnl_accumulates_across_multiple_partial_sells():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=15.0, timestamp=NOW)
    # First sale: 50 tokens * (15-10) = 250
    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=20.0, timestamp=NOW)
    # Second sale: 25 tokens * (20-10) = 250
    assert pos.realized_pnl == pytest.approx(500.0)


def test_selling_100_percent_fully_closes_position():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=1.0, sell_price=15.0, timestamp=NOW)

    assert pos.status == "CLOSED"
    assert pos.remaining_size == pytest.approx(0.0)


def test_cannot_sell_from_an_already_closed_position():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=1.0, sell_price=15.0, timestamp=NOW)

    with pytest.raises(ValueError):
        take_partial_profit(pos, sell_fraction=0.1, sell_price=16.0, timestamp=NOW)


def test_sell_fraction_must_be_in_valid_range():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    with pytest.raises(ValueError):
        take_partial_profit(pos, sell_fraction=0.0, sell_price=15.0, timestamp=NOW)
    with pytest.raises(ValueError):
        take_partial_profit(pos, sell_fraction=1.5, sell_price=15.0, timestamp=NOW)
    with pytest.raises(ValueError):
        take_partial_profit(pos, sell_fraction=-0.1, sell_price=15.0, timestamp=NOW)


def test_profit_taking_history_records_every_sale():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.2, sell_price=15.0, timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=18.0, timestamp=NOW)

    assert len(pos.profit_taking_history) == 2
    assert pos.profit_taking_history[0]["sell_fraction"] == 0.2
    assert pos.profit_taking_history[1]["sell_fraction"] == 0.5


def test_partial_sell_does_not_mutate_the_input_position():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    updated = take_partial_profit(
        pos, sell_fraction=0.2, sell_price=15.0, timestamp=NOW
    )

    assert pos.remaining_size == 100.0  # original untouched
    assert updated.remaining_size == pytest.approx(80.0)


def test_selling_at_a_loss_produces_negative_realized_pnl():
    pos = open_position("TokenA", entry_price=10.0, size=100, entry_timestamp=NOW)
    pos = take_partial_profit(pos, sell_fraction=0.5, sell_price=6.0, timestamp=NOW)
    # 50 tokens * (6-10) = -200
    assert pos.realized_pnl == pytest.approx(-200.0)
