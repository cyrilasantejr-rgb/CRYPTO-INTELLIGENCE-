"""
Read-only data access layer for the Phase 15 dashboard.

Deliberately contains NO new business logic - every function here
calls an existing, already-tested module (PositionStore, position_math,
BirdeyeRealtimePriceAdapter, compute_recommendation, discovery's
bronze_reader) and reshapes the result into something simple for the
dashboard to render. If the underlying decision/PnL/position/discovery
logic ever changes, it changes in ONE place (its original module) and
both the CLI tools and this dashboard automatically stay in sync.

Every function takes explicit inputs and returns plain data (dicts,
dataclasses) - no Streamlit imports here at all. This keeps this module
testable with plain pytest, with no need to spin up a Streamlit app to
verify it works.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from common.storage.object_store import ObjectStoreClient
from decision_engine.decision_logic import Recommendation
from decision_engine.run_decision_check import compute_recommendation
from discovery.bronze_reader import read_latest_valid_candidates
from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter
from paper_trading.position_math import unrealized_pnl, unrealized_pnl_pct
from paper_trading.position_store import PositionStore

logger = logging.getLogger(__name__)


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


# First-slice watchlist: a small, explicit list of tokens you're
# personally tracking but don't hold a position in yet. Hardcoded,
# not stored in a database - deliberately simple for this first slice.
# A real add/remove-able watchlist (its own SQLite table) is a natural
# next step once this display logic is verified working.
WATCHLIST_TOKENS: list[str] = [
    "So11111111111111111111111111111111111111112",  # wrapped SOL
]


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


# Real discovery runs can surface dozens of valid candidates; each one
# costs TWO live API calls when enriched (price + recommendation).
# Capped here, not in bronze_reader.py, since this is a DASHBOARD
# rendering/cost concern specifically, not a fact about the data itself.
MAX_DISCOVERED_WATCHLIST_SIZE = 10


def get_discovered_watchlist_tokens() -> list[str]:
    """
    Token addresses from the most recent discovery Bronze partition,
    valid (non-quarantined) only - see discovery/bronze_reader.py.

    Deliberately reads from Bronze rather than calling
    discover_candidates() live: a screener call costs 75 CU and can
    return up to 100 NEW addresses on every call. Re-running it live on
    every Streamlit render (every click, every page refresh) would be
    slow and wasteful compared to reading what a periodic discovery run
    already persisted.

    Returns [] (not an exception) if MinIO/S3 is unreachable or no
    discovery data exists yet - same graceful-degradation pattern as
    get_recommendation_for_token. An optional data source failing
    should never take down the rest of the dashboard.
    """
    try:
        store = ObjectStoreClient(
            bucket=os.environ.get("S3_BUCKET_NAME", "crypto-intelligence"),
            endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("MINIO_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("MINIO_SECRET_KEY"),
        )
        candidates = read_latest_valid_candidates(store)
        # Cap and rank by volume_24h_usd - a real discovery run can
        # return dozens of valid candidates, and each one costs TWO
        # live API calls when enriched below (get_current_price +
        # get_recommendation_for_token). Rendering all of them on every
        # dashboard click/refresh would be slow and API-expensive.
        # Ranking by the same volume metric discovery itself sorts by
        # means "top N" reflects real dollar activity, not S3 listing
        # order (which is arbitrary).
        candidates.sort(key=lambda c: c.payload.get("volume_24h_usd", 0), reverse=True)
        top_candidates = candidates[:MAX_DISCOVERED_WATCHLIST_SIZE]
        return [c.token_address for c in top_candidates]
    except Exception:
        # source (discovery) must never crash the dashboard's core
        # positions/watchlist rendering.
        logger.warning("Could not read discovery candidates from Bronze", exc_info=True)
        return []


@dataclass
class WatchlistItem:
    """A token being tracked with no open position - price and
    recommendation only, no P&L fields since there is no position.

    source distinguishes how this token ended up on the watchlist:
    "manual" (in WATCHLIST_TOKENS) or "discovered" (found by the
    discovery engine's most recent Bronze-persisted run) - shown in
    the UI so an unfamiliar address always has a visible reason for
    being there, rather than looking like an unexplained addition.
    """

    token_address: str
    current_price: float | None
    recommendation: Recommendation | None
    source: str


def get_watchlist() -> list[WatchlistItem]:
    """
    Fetches price and recommendation for each token in WATCHLIST_TOKENS
    plus every token the discovery engine's most recent run marked
    valid. Deduplicated (a manually-added token that discovery also
    finds is shown once, tagged "manual" since it was added first).
    Same graceful-degradation pattern as get_open_positions - one token
    failing does not block the rest.
    """
    discovered = get_discovered_watchlist_tokens()

    seen: set[str] = set()
    ordered_tokens: list[tuple[str, str]] = []
    for token_address in WATCHLIST_TOKENS:
        if token_address not in seen:
            seen.add(token_address)
            ordered_tokens.append((token_address, "manual"))
    for token_address in discovered:
        if token_address not in seen:
            seen.add(token_address)
            ordered_tokens.append((token_address, "discovered"))

    items = []
    for token_address, source in ordered_tokens:
        price = get_current_price(token_address)
        recommendation = get_recommendation_for_token(token_address)
        items.append(
            WatchlistItem(
                token_address=token_address,
                current_price=price,
                recommendation=recommendation,
                source=source,
            )
        )
    return items
