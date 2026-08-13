"""
Phase 13: CLI for opening, partially selling, closing, and checking the
status of paper positions. Reuses Phase 8's BirdeyeRealtimePriceAdapter
for live price checks - the same adapter built for the streaming
producer now pays off again here, a real payoff of the adapter-
interface pattern used throughout this project.

Usage:

    python3 -m paper_trading.run_paper_trade open --token <address> --price <p> --size <s>
    python3 -m paper_trading.run_paper_trade status
    python3 -m paper_trading.run_paper_trade sell --id <position_id> --fraction 0.2
    python3 -m paper_trading.run_paper_trade close --id <position_id>
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from ingestion.market.birdeye_realtime_adapter import BirdeyeRealtimePriceAdapter
from paper_trading.position_math import (
    open_position,
    position_value,
    take_partial_profit,
    unrealized_pnl,
    unrealized_pnl_pct,
    update_highest_price,
)
from paper_trading.position_store import PositionStore, now_utc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_live_price(token_address: str) -> float | None:
    load_dotenv()
    api_key = os.environ.get("BIRDEYE_API_KEY")
    if not api_key:
        logger.warning("BIRDEYE_API_KEY not set - cannot fetch live price")
        return None
    try:
        adapter = BirdeyeRealtimePriceAdapter(api_key=api_key)
        envelopes = list(adapter.fetch_latest_prices([token_address]))
        if not envelopes:
            return None
        return envelopes[0].payload.get("value")
    except Exception:
        logger.exception("Failed to fetch live price for %s", token_address)
        return None


def cmd_open(args: argparse.Namespace) -> None:
    store = PositionStore()
    pos = open_position(
        token_address=args.token,
        entry_price=args.price,
        size=args.size,
        entry_timestamp=now_utc(),
    )
    position_id = store.save(pos)
    logger.info(
        "Opened position #%d: %s @ %.6f, size=%.4f",
        position_id,
        args.token,
        args.price,
        args.size,
    )


def cmd_status(args: argparse.Namespace) -> None:
    store = PositionStore()
    positions = store.list_open_positions()

    if not positions:
        logger.info("No open positions.")
        return

    for position_id, pos in positions:
        current_price = _get_live_price(pos.token_address)

        logger.info("--- Position #%d: %s ---", position_id, pos.token_address)
        logger.info(
            "Entry price: %.6f | Remaining size: %.4f",
            pos.entry_price,
            pos.remaining_size,
        )
        logger.info("Highest price since entry: %.6f", pos.highest_price_since_entry)
        logger.info("Realized P&L so far: %.4f", pos.realized_pnl)

        if current_price is None:
            logger.warning(
                "Could not fetch live price - unrealized P&L unavailable this run"
            )
            continue

        pos = update_highest_price(pos, current_price)
        store.save(pos, position_id=position_id)

        logger.info("Current price: %.6f", current_price)
        logger.info(
            "Unrealized P&L: %.4f (%.2f%%)",
            unrealized_pnl(pos, current_price),
            unrealized_pnl_pct(pos, current_price) * 100,
        )
        logger.info("Current position value: %.4f", position_value(pos, current_price))


def cmd_sell(args: argparse.Namespace) -> None:
    store = PositionStore()
    pos = store.get(args.id)
    if pos is None:
        logger.error("No position with id %d", args.id)
        return

    sell_price = (
        args.price if args.price is not None else _get_live_price(pos.token_address)
    )
    if sell_price is None:
        logger.error("No sell price given and could not fetch a live price - aborting")
        return

    pos = take_partial_profit(
        pos, sell_fraction=args.fraction, sell_price=sell_price, timestamp=now_utc()
    )
    store.save(pos, position_id=args.id)

    logger.info(
        "Sold %.0f%% of position #%d @ %.6f. Remaining size: %.4f. Status: %s",
        args.fraction * 100,
        args.id,
        sell_price,
        pos.remaining_size,
        pos.status,
    )
    logger.info("Cumulative realized P&L for this position: %.4f", pos.realized_pnl)


def cmd_close(args: argparse.Namespace) -> None:
    args.fraction = 1.0
    cmd_sell(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper trading CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="Open a new paper position")
    open_parser.add_argument("--token", required=True)
    open_parser.add_argument("--price", required=True, type=float)
    open_parser.add_argument("--size", required=True, type=float)
    open_parser.set_defaults(func=cmd_open)

    status_parser = subparsers.add_parser("status", help="Show all open positions")
    status_parser.set_defaults(func=cmd_status)

    sell_parser = subparsers.add_parser(
        "sell", help="Take partial profit on a position"
    )
    sell_parser.add_argument("--id", required=True, type=int)
    sell_parser.add_argument("--fraction", required=True, type=float)
    sell_parser.add_argument(
        "--price",
        required=False,
        type=float,
        default=None,
        help="Omit to use live price",
    )
    sell_parser.set_defaults(func=cmd_sell)

    close_parser = subparsers.add_parser("close", help="Fully close a position")
    close_parser.add_argument("--id", required=True, type=int)
    close_parser.add_argument(
        "--price",
        required=False,
        type=float,
        default=None,
        help="Omit to use live price",
    )
    close_parser.set_defaults(func=cmd_close)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
