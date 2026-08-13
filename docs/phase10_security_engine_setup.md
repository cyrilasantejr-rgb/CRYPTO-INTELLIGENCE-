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

The token security endpoint's exact response field names aren't
confirmed against a live response yet (see ADR-026) - the runner checks
several plausible field name variants and logs the raw payload if none
match, so a quick field-name fix (like the ones needed for the holder
endpoint earlier tonight) may be needed after the first real run.
