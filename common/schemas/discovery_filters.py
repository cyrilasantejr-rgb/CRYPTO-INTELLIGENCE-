"""
Vendor-agnostic filter criteria for token discovery/screening.

Lives here (not inside discovery/birdeye_discovery_adapter.py) because
common/interfaces/source_adapter.py's TokenDiscoveryAdapter.discover_candidates()
needs to reference this type, and the interface layer must never depend
on a concrete vendor adapter - that would invert the entire point of
the Adapter pattern (interfaces depend on nothing; concrete adapters
depend on interfaces, never the reverse). If a second discovery vendor
is added later (e.g. DexScreener), it reuses this exact same shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiscoveryFilters:
    """
    Query parameters for a token screener. Every min_/max_ field is
    optional (None = do not send that filter to the vendor at all) -
    see to_params() for why that distinction matters.

    Defaults target small-cap tokens with real trading activity, not
    the whole market. max_liquidity is what actually excludes blue
    chips (SOL, USDC) - that ceiling defines the universe FIRST. Only
    then does sort_by="volume_24h_usd" rank within it, by absolute
    dollar activity.

    An earlier version of this sorted by volume_24h_change_percent
    (percent change) instead - reverted after live testing showed it is
    numerically unstable near a zero baseline: a token trading for the
    first time has ~$0 prior-period volume, so ANY volume at all reads
    as a multi-billion-percent change (confirmed live: 7,584,390,951%
    on a token whose entire trade history was ~4 hours old) and
    dominates the ranking regardless of real quality. Percent-change
    fields remain useful as FLOORS on a future filter, but should not
    be the primary sort key when the underlying baseline can be ~zero.

    min_volume_24h_usd, min_holder, and min_trade_24h_count remain as
    floors against dead pools and wash-trade noise. These thresholds
    are heuristic starting points, not empirically derived - expect to
    tune them once Phase 5's backtesting framework can evaluate which
    filter combinations actually preceded good entries historically.

    to_params() is Birdeye-shaped (min_liquidity, sort_by, etc. match
    Birdeye's exact query param names) since Birdeye is currently the
    only discovery vendor. If a second vendor is added, either that
    vendor's params happen to match, or to_params() moves into each
    adapter and this class becomes purely the shared field definitions.
    """

    sort_by: str = "volume_24h_usd"
    sort_type: str = "desc"
    min_liquidity: float | None = 5_000
    max_liquidity: float | None = 2_000_000
    min_volume_24h_usd: float | None = 10_000
    min_holder: int | None = 50
    min_trade_24h_count: int | None = 50
    limit: int = 50
    offset: int = 0
    chain: str = "solana"

    def to_params(self) -> dict:
        """
        Build the query-param dict for this request, OMITTING any
        field that is None.

        Why this matters: "filter for records >= this value" is what
        min_liquidity etc. mean when present. If we sent min_liquidity=0
        instead of omitting it, that is still a valid, deliberate filter
        (0 is a real number) - but None means "the user did not ask to
        filter on this at all," which is a different thing. Sending a
        key with value None/null would either error or be misread by
        the vendor, so we must drop the key entirely, not send it empty.
        """
        params: dict = {
            "sort_by": self.sort_by,
            "sort_type": self.sort_type,
            "limit": min(self.limit, 100),
            "offset": self.offset,
        }
        optional = {
            "min_liquidity": self.min_liquidity,
            "max_liquidity": self.max_liquidity,
            "min_volume_24h_usd": self.min_volume_24h_usd,
            "min_holder": self.min_holder,
            "min_trade_24h_count": self.min_trade_24h_count,
        }
        for key, value in optional.items():
            if value is not None:
                params[key] = value
        return params
