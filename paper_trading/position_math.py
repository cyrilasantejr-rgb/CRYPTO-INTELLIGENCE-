"""
Pure position P&L math - no I/O, no database. Given how directly this
affects whether a person trusts their own paper-trading numbers, this
is exactly the kind of logic worth verifying thoroughly before trusting
it - same lesson as the holder-concentration denominator bug earlier
tonight (ADR-024), where code that ran without error still produced a
wrong number until actually checked against real-world intuition.

KEY DESIGN DECISION, stated explicitly rather than left ambiguous:
"sell 20%" (from the project's original example: "sell 20%, hold 80%")
means 20% of the CURRENT remaining position size, not 20% of the
original entry size. If you've already sold once, a second "sell 20%"
sells 20% of what's left NOW, not 20% of what you originally bought.
This matches how a person would naturally describe staged profit-taking
in conversation, and is the only interpretation that stays well-defined
after multiple partial sells (a fixed original-size percentage could
try to sell more than remains, after enough partial sells).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    token_address: str
    entry_price: float
    entry_timestamp: datetime
    initial_size: float  # token amount at entry
    remaining_size: (
        float  # token amount still held (initial_size minus any partial sells)
    )
    highest_price_since_entry: float
    realized_pnl: float = 0.0  # cumulative $ P&L from all partial/full sells so far
    profit_taking_history: list[dict] = field(default_factory=list)
    status: str = "OPEN"  # OPEN or CLOSED


def open_position(
    token_address: str, entry_price: float, size: float, entry_timestamp: datetime
) -> Position:
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price}")
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")

    return Position(
        token_address=token_address,
        entry_price=entry_price,
        entry_timestamp=entry_timestamp,
        initial_size=size,
        remaining_size=size,
        highest_price_since_entry=entry_price,
    )


def update_highest_price(position: Position, current_price: float) -> Position:
    """Returns a position with highest_price_since_entry updated if
    current_price is a new high - used for trailing-stop logic. Does
    NOT mutate the input; callers persist the returned copy."""
    new_high = max(position.highest_price_since_entry, current_price)
    return _replace(position, highest_price_since_entry=new_high)


def unrealized_pnl(position: Position, current_price: float) -> float:
    """Dollar P&L on the position's currently-remaining size only - a
    fully or partially closed position's already-realized P&L is
    tracked separately in position.realized_pnl, never double-counted
    here."""
    return position.remaining_size * (current_price - position.entry_price)


def unrealized_pnl_pct(position: Position, current_price: float) -> float:
    """Percentage P&L, independent of position size - lets a caller
    show '+23%' regardless of how much was originally invested."""
    return (current_price - position.entry_price) / position.entry_price


def position_value(position: Position, current_price: float) -> float:
    return position.remaining_size * current_price


def take_partial_profit(
    position: Position, sell_fraction: float, sell_price: float, timestamp: datetime
) -> Position:
    """
    Sells sell_fraction of the CURRENT remaining size (see module
    docstring for why this is the chosen semantic, not a fraction of
    the original entry size). Returns a new Position - does not mutate
    the input.

    sell_fraction == 1.0 fully closes the position (status -> CLOSED).
    """
    if not 0 < sell_fraction <= 1.0:
        raise ValueError(f"sell_fraction must be in (0, 1.0], got {sell_fraction}")
    if position.status == "CLOSED":
        raise ValueError("Cannot sell from a position that is already CLOSED")

    amount_sold = position.remaining_size * sell_fraction
    realized_this_sale = amount_sold * (sell_price - position.entry_price)
    new_remaining = position.remaining_size - amount_sold

    new_history = position.profit_taking_history + [
        {
            "timestamp": timestamp.isoformat(),
            "sell_fraction": sell_fraction,
            "amount_sold": amount_sold,
            "sell_price": sell_price,
            "realized_pnl": realized_this_sale,
        }
    ]

    new_status = "CLOSED" if new_remaining <= 1e-12 else "OPEN"
    # 1e-12 threshold, not == 0, to tolerate ordinary floating-point
    # rounding after a sell_fraction=1.0 full close - exact zero
    # equality on floats is unreliable.

    return _replace(
        position,
        remaining_size=max(new_remaining, 0.0),
        realized_pnl=position.realized_pnl + realized_this_sale,
        profit_taking_history=new_history,
        status=new_status,
    )


def _replace(position: Position, **changes) -> Position:
    """Small helper mirroring dataclasses.replace - used instead of
    calling dataclasses.replace directly so list fields (like
    profit_taking_history) are handled predictably rather than relying
    on dataclasses.replace's shallow-copy behavior for mutable defaults."""
    from dataclasses import replace

    return replace(position, **changes)
