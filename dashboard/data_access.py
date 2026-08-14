"""
Read-only data access layer for the Phase 15 dashboard.

Deliberately contains NO new business logic - every function here
calls an existing, already-tested module (PositionStore, position_math,
BirdeyeRealtimePriceAdapter, compute_recommendation) and reshapes the
result into something simple for the dashboard to render. If the
underlying decision/PnL/position logic ever changes, it changes in ONE
place (its original module) and both the CLI tools and this dashboard
automatically stay in sync.

Every function takes explicit inputs and returns plain data (dicts,
dataclasses) - no Streamlit imports here at all. This keeps this module
testable with plain pytest, with no need to spin up a Streamlit app to
verify it works.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from decision_engine.decision_logic import Recommendation
from decision_engine.run_decision_check import compute_recommendation
from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter
from paper_trading.position_math import unrealized_pnl, unrealized_pnl_pct
from paper_trading.position_store import PositionStore


@dataclass
class PositionView:
    """Everything the dashboard needs to render one open position,
    already combined: stored position data + live price + computed P&L.
    A plain dataclass, not a Streamlit-specific type - keeps this
    module UI-framework-agnostic."""

    position_id: int
    token_address: str
    entry_price: float
    current_price: float | None
    remaining_size: float
    unrealized_pnl_usd: float | None
    unrealized_pnl_pct: float | None
    status: str


def get_current_price(token_address: str, api_key: str | None = None) -> float | None:
    """Fetches ONE token's current price via the same Birdeye adapter
    Phase 8's streaming producer uses. Returns None (not an exception)
    if the price is unavailable, so callers can render 'price
    unavailable' rather than crashing the whole dashboard over one
    flaky API call."""
    load_dotenv()
    api_key = api_key or os.environ.get("BIRDEYE_API_KEY")
    if not api_key:
        return None

    adapter = BirdeyeRealtimePriceAdapter(api_key=api_key)
    for envelope in adapter.fetch_latest_prices([token_address]):
        return envelope.payload.get("value")
    return None


def get_open_positions() -> list[PositionView]:
    """Reads all OPEN positions from the real position store, enriches
    each with a live price and computed P&L. If a price fetch fails for
    one token, that position still appears with current_price=None and
    pnl=None rather than being dropped silently."""
    store = PositionStore()
    views = []

    for position_id, position in store.list_open_positions():
        current_price = get_current_price(position.token_address)

        pnl_usd = None
        pnl_pct = None
        if current_price is not None:
            pnl_usd = unrealized_pnl(position, current_price)
            pnl_pct = unrealized_pnl_pct(position, current_price)

        views.append(
            PositionView(
                position_id=position_id,
                token_address=position.token_address,
                entry_price=position.entry_price,
                current_price=current_price,
                remaining_size=position.remaining_size,
                unrealized_pnl_usd=pnl_usd,
                unrealized_pnl_pct=pnl_pct,
                status=position.status,
            )
        )

    return views


def get_recommendation_for_token(
    token_address: str,
    dev_wallet: str | None = None,
    news_topic: str | None = None,
) -> Recommendation | None:
    """Calls the SAME compute_recommendation() the CLI tool uses - no
    duplicated decision logic. Returns None (rather than raising) on
    failure, since a dashboard should degrade gracefully instead of
    crashing the whole page over one token's API failure."""
    try:
        return compute_recommendation(token_address, dev_wallet, news_topic)
    except Exception:  # noqa: BLE001 - intentionally broad: a read-only
        # dashboard must stay usable even if one token API call fails
        # in an unexpected way. Narrowing this would let one bad token
        # crash the whole page instead of showing one missing card.
        return None
