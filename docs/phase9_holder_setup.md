# Phase 9 (first slice): Holder Concentration Analysis

Analyzes how concentrated a token's ownership is - a real rug-pull-risk
signal (heavy concentration in a few wallets is one of the clearest red
flags). Uses the same Birdeye account/API key already set up since
Phase 1 - no new vendor signup needed for this piece.

## What this covers, and what it doesn't (yet)

This slice covers: fetching top holders for a token and computing
concentration metrics (top-10%, HHI, risk tier).

NOT yet covered (real future work, not silently skipped): dev-wallet
transaction monitoring, whale wallet transaction history, wallet
clustering/funding-relationship analysis. Those genuinely need a
provider like Helius (on-chain transaction data), which hasn't been set
up in this project yet.

## Running it

```
source venv/bin/activate
python3 -m wallet_intelligence.run_holder_analysis --token So11111111111111111111111111111111111111112
```

Prints holder count, computed top-10% concentration, Birdeye's own
reported top-10% (for comparison), HHI, and a risk tier
(LOW/MODERATE/HIGH/VERY_HIGH/CRITICAL).

## Known limitation

Unlike every scripted phase before this one, this adapter's real API
call has not been run against the live Birdeye endpoint before being
handed off (same situation as Phase 8's Kafka wiring) - this command
will be the first real test of it. If the response shape doesn't quite
match what's expected (field names like `items`, `ui_amount`,
`top10HoldPercent`), that'll show up as a warning about "no holder
amounts returned" rather than a crash - the code was written to fail
informatively, not silently, if the API's actual response differs from
what the docs describe.
