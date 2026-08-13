"""
Pure dev-wallet outflow detection - no I/O. Takes a wallet's parsed
transaction history (from HeliusWalletAdapter) and detects transfers of
a SPECIFIC token OUT of that wallet - the core signal for "is this dev/
whale wallet selling or moving out their holdings?"

Deliberately narrow in scope for this first slice: detects raw token
outflows (transfers where the wallet is the sender). Does NOT yet
distinguish "sold on a DEX" from "transferred to another wallet they
also control" from "sent to an exchange" - all three are outflows from
this wallet's perspective, but have different real-world meaning. That
distinction needs either DEX-swap-specific parsing or wallet-clustering
analysis (tracing where the recipient address's funds go next) - both
genuinely separate, larger pieces of work, not built here. What's built
here answers the narrower, still-valuable question: "is token leaving
this wallet, and how much, and how recently?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class OutflowSummary:
    wallet_address: str
    token_address: str
    total_outflow_amount: float
    outflow_transaction_count: int
    most_recent_outflow: datetime | None
    has_recent_outflow: bool  # any outflow within the given recency window


def analyze_outflows(
    transactions: list[dict],
    wallet_address: str,
    token_address: str,
    recency_window_hours: float = 24.0,
    now: datetime | None = None,
) -> OutflowSummary:
    """
    transactions: Helius's parsed transaction list (each item expected to
    have a "tokenTransfers" list and a "timestamp" unix-seconds field -
    see ADR-025 for the honest caveat that these exact field names are
    based on Helius's documented schema, not yet verified against a live
    response, same situation Birdeye integrations were in before their
    first real run tonight).

    wallet_address: the wallet being monitored (e.g. a token's dev wallet).
    token_address: the SPECIFIC token mint to watch for outflows of - a
    dev wallet's history will contain many unrelated transactions
    (SOL transfers, other tokens, NFTs); only outflows of this one token
    matter for "is the dev selling THIS token."
    """
    now = now or datetime.now(timezone.utc)

    total_outflow = 0.0
    outflow_count = 0
    most_recent: datetime | None = None

    for tx in transactions:
        tx_timestamp = tx.get("timestamp")
        tx_time = (
            datetime.fromtimestamp(tx_timestamp, tz=timezone.utc)
            if tx_timestamp is not None
            else None
        )

        for transfer in tx.get("tokenTransfers", []):
            if (
                transfer.get("mint") == token_address
                and transfer.get("fromUserAccount") == wallet_address
            ):
                amount = transfer.get("tokenAmount", 0.0) or 0.0
                total_outflow += amount
                outflow_count += 1
                if tx_time is not None and (
                    most_recent is None or tx_time > most_recent
                ):
                    most_recent = tx_time

    has_recent = (
        most_recent is not None
        and (now - most_recent).total_seconds() <= recency_window_hours * 3600
    )

    return OutflowSummary(
        wallet_address=wallet_address,
        token_address=token_address,
        total_outflow_amount=total_outflow,
        outflow_transaction_count=outflow_count,
        most_recent_outflow=most_recent,
        has_recent_outflow=has_recent,
    )
