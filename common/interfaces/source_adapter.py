"""
Common interface every vendor-specific market data adapter implements.

Why this exists: downstream code (Kafka producers, S3 writers, Airflow tasks)
should depend on `MarketDataAdapter`, never on `BirdeyeAdapter` directly. If we
add CoinGecko or drop Birdeye later, only this one file's contract has to hold -
nothing else in the system changes. This is the Adapter pattern (also called
Ports-and-Adapters / Hexagonal Architecture): the "port" is this abstract class,
the "adapter" is each vendor-specific implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime

from common.schemas.envelope import BronzeEnvelope


class MarketDataAdapter(ABC):
    """Interface for any vendor that provides historical OHLCV/price data."""

    #: Set by each concrete adapter, e.g. "birdeye". Used as the `source` field
    #: on every BronzeEnvelope this adapter produces.
    source_name: str

    @abstractmethod
    def fetch_historical_ohlcv(
        self,
        token_address: str,
        start: datetime,
        end: datetime,
        interval: str = "1h",
    ) -> Iterator[BronzeEnvelope]:
        """
        Fetch historical OHLCV candles for a token between start and end,
        yielding one BronzeEnvelope per candle.

        Implementations are responsible for:
          - chunking requests if the vendor caps records per call
          - retrying transient failures with backoff
          - NOT raising on a single bad candle - skip and log it instead,
            so one malformed record doesn't kill an entire historical backfill
        """
        raise NotImplementedError


class RealtimePriceAdapter(ABC):
    """
    Interface for any vendor that provides current/latest prices for a
    watchlist of tokens, via polling (not push/WebSocket).

    Deliberately a SEPARATE interface from MarketDataAdapter, not a new
    method bolted onto it: "give me the current price for N tokens right
    now" is a fundamentally different shape of request than "give me
    historical candles for one token over a date range" - different
    batching (many tokens per call vs one token per call), different
    caller (a polling loop vs a one-shot backfill), different vendor
    endpoint entirely. Forcing both into one interface would make neither
    shape clean.
    """

    source_name: str

    @abstractmethod
    def fetch_latest_prices(
        self, token_addresses: list[str]
    ) -> Iterator[BronzeEnvelope]:
        """
        Fetch the current price for each address in token_addresses,
        yielding one BronzeEnvelope per token. Implementations should
        batch this into as few vendor API calls as the vendor's API
        allows, rather than one call per token.
        """
        raise NotImplementedError


class HolderDataAdapter(ABC):
    """
    Interface for any vendor that provides token holder distribution
    data - who owns how much of a given token, used for concentration/
    rug-risk analysis in Phase 9/10.

    Separate interface, not bolted onto MarketDataAdapter or
    RealtimePriceAdapter, for the same reason those two are separate from
    each other: "who holds this token and how much" is a fundamentally
    different question from price history or current price, with a
    different vendor endpoint and a different response shape.
    """

    source_name: str

    @abstractmethod
    def fetch_top_holders(self, token_address: str, limit: int = 100) -> BronzeEnvelope:
        """
        Fetch the top `limit` holders (grouped by owner wallet, not raw
        token account) for a token, returning one BronzeEnvelope whose
        payload contains the full holder list plus any vendor-provided
        summary stats (e.g. top-10 concentration percentage).
        """
        raise NotImplementedError


class WalletTransactionAdapter(ABC):
    """
    Interface for any vendor that provides parsed/enhanced transaction
    history for a Solana wallet - a fundamentally different domain from
    token price/holder data (MarketDataAdapter, RealtimePriceAdapter,
    HolderDataAdapter above): this is about WALLET BEHAVIOR over time,
    not token-level snapshots.
    """

    source_name: str

    @abstractmethod
    def fetch_transactions(
        self, wallet_address: str, limit: int = 100
    ) -> BronzeEnvelope:
        """
        Fetch recent transaction history for a wallet, returning one
        BronzeEnvelope whose payload contains the raw list of parsed
        transactions (whatever shape the vendor's "enhanced"/parsed
        format provides - token transfers, timestamps, transaction type).
        """
        raise NotImplementedError
