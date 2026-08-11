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
