"""Unit tests for dashboard/data_access.py."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from dashboard.data_access import (
    PositionView,
    get_current_price,
    get_open_positions,
)
from paper_trading.position_math import Position


def _make_position(entry_price: float = 100.0) -> Position:
    return Position(
        token_address="TESTTOKEN",
        entry_price=entry_price,
        entry_timestamp=datetime.now(timezone.utc),
        initial_size=10.0,
        remaining_size=10.0,
        highest_price_since_entry=entry_price,
        realized_pnl=0.0,
        profit_taking_history=[],
        status="OPEN",
    )


def test_get_current_price_returns_none_without_api_key():
    with patch("dashboard.data_access.load_dotenv"), patch.dict(
        "os.environ", {}, clear=True
    ):
        price = get_current_price("TESTTOKEN", api_key=None)
    assert price is None


def test_get_open_positions_includes_pnl_when_price_available():
    fake_position = _make_position(entry_price=100.0)

    with patch("dashboard.data_access.PositionStore") as mock_store_cls, patch(
        "dashboard.data_access.get_current_price", return_value=110.0
    ):
        mock_store = mock_store_cls.return_value
        mock_store.list_open_positions.return_value = [(1, fake_position)]

        views = get_open_positions()

    assert len(views) == 1
    view = views[0]
    assert isinstance(view, PositionView)
    assert view.position_id == 1
    assert view.current_price == 110.0
    assert view.unrealized_pnl_usd > 0
    assert view.unrealized_pnl_pct > 0


def test_get_open_positions_handles_missing_price_gracefully():
    fake_position = _make_position(entry_price=100.0)

    with patch("dashboard.data_access.PositionStore") as mock_store_cls, patch(
        "dashboard.data_access.get_current_price", return_value=None
    ):
        mock_store = mock_store_cls.return_value
        mock_store.list_open_positions.return_value = [(1, fake_position)]

        views = get_open_positions()

    assert len(views) == 1
    view = views[0]
    assert view.current_price is None
    assert view.unrealized_pnl_usd is None
    assert view.unrealized_pnl_pct is None
