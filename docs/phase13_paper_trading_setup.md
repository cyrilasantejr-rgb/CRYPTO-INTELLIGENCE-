# Phase 13: Paper Trading

Tracks paper positions (no real money) - open, partial profit-taking,
close - persisted locally in SQLite so they survive between separate
runs. See ADR-033 for why SQLite instead of the PostgreSQL mentioned in
the original design, and ADR-034 for the exact meaning of "sell X%" once
you've already sold some of a position.

## Opening a position

```
source venv/bin/activate
python3 -m paper_trading.run_paper_trade open --token So11111111111111111111111111111111111111112 --price 75.80 --size 10
```

Prints the new position's id - you'll need this for `sell`/`close`.

## Checking status (all open positions, with live P&L)

```
python3 -m paper_trading.run_paper_trade status
```

Fetches the current live price (via Phase 8's Birdeye adapter) for each
open position and shows unrealized P&L, current value, and the highest
price seen since entry.

## Taking partial profit

```
python3 -m paper_trading.run_paper_trade sell --id 1 --fraction 0.2
```

Sells 20% of whatever is CURRENTLY held in position #1 (not 20% of the
original entry size - see ADR-034). Omit `--price` to use the live
price automatically, or pass `--price` explicitly to simulate a
specific sale price.

## Closing a position entirely

```
python3 -m paper_trading.run_paper_trade close --id 1
```

Equivalent to `sell --fraction 1.0` - sells everything remaining.

## Known limitation

This is not yet wired into Phase 12's decision engine - opening a
position based on a `BUY` recommendation, or automatically checking
`TAKE_PARTIAL_PROFIT`/`EXIT` recommendations against open positions, is
real future work (this would be what finally unlocks the full action
vocabulary Phase 12 honestly couldn't produce without position
awareness). Right now, opening/selling/closing are manual CLI actions.
