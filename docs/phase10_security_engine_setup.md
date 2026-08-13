# Phase 10: Composite Security/Rug-Risk Engine

Combines every signal built across Phase 9 (holder concentration,
dev-wallet outflow monitoring) with token security metadata (mint/
freeze authority) into one final, explainable RUG_RISK_SCORE (0-100).

## Running it

Full check, including dev-wallet monitoring (needs a known dev/creator
wallet address):

```
source venv/bin/activate
python3 -m rug_pull_intelligence.run_security_check --token <token_address> --dev-wallet <wallet_address>
```

Without a known dev wallet (holder concentration + security metadata
only - dev-wallet outflow recorded as a data gap, not silently skipped):

```
python3 -m rug_pull_intelligence.run_security_check --token <token_address>
```

Example using wrapped SOL:

```
python3 -m rug_pull_intelligence.run_security_check --token So11111111111111111111111111111111111111112
```

## How the score is built

Each signal contributes points only if it's actually available and
actually flags a concern - see `rug_pull_intelligence/security_scoring.py`
for the exact point values and ADR-026 for why no single signal can push
a token straight to CRITICAL alone. The output always shows:
- The final score and tier (LOW/MODERATE/HIGH/VERY_HIGH/CRITICAL)
- A human-readable list of exactly which signals contributed and why
- Any data gaps (signals that couldn't be fetched this run) - never
  silently hidden, since a score built on partial data should be
  understood as less complete, not treated as equally reliable

## Known limitation

The token security endpoint (`/defi/token_security`) requires a paid
Birdeye tier - confirmed via a real 401 response using the same key
that works on every other endpoint in this project (see ADR-027). This
is NOT a bug and NOT something you need to fix: the composite scoring
engine was built from the start to treat a missing signal as an honest,
labeled data gap rather than a crash or an assumed worst case, and this
is exactly that design working correctly under real conditions.

Running the security check will still produce a complete, correctly-
scored report - just with mint/freeze authority listed under "Data
gaps" rather than contributing to the score. Holder concentration and
dev-wallet outflow monitoring (if a dev wallet is provided) are
unaffected and will still work normally.

A genuine free alternative exists as future work (see ADR-027): mint
and freeze authority are on-chain SPL Token mint account fields,
queryable directly via any Solana RPC's `getAccountInfo` - including
Helius's own RPC, already set up in this project - with no paid tier
needed. Not built yet; a clear next step, not a dead end.
