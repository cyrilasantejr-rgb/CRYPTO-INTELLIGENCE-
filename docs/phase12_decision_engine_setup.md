# Phase 12 (first slice): Decision Engine

Combines the security engine (Phase 10), the entry ML model (Phase 6,
currently unused - see below), and a news check (Phase 11) into one
final, explainable action recommendation.

## Scope, stated plainly

Only produces `BUY`, `HOLD`, or `AVOID` - not the full action
vocabulary from the original design (`ADD`, `TAKE_PARTIAL_PROFIT`,
`REDUCE_POSITION`, `EXIT`). Those genuinely need to know whether a
position is already held, which requires Phase 13 (paper trading), not
built yet. When a critical risk fires, the output tells you this would
be an `EMERGENCY_EXIT` if you were already holding - real information,
not a guessed action.

## Running it

```
source venv/bin/activate
python3 -m decision_engine.run_decision_check --token <token_address> --dev-wallet <wallet_address> --news-topic Solana
```

`--dev-wallet` and `--news-topic` are both optional.

Example using wrapped SOL:

```
python3 -m decision_engine.run_decision_check --token So11111111111111111111111111111111111111112 --dev-wallet 5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1 --news-topic Solana
```

## How overrides work

Checked FIRST, before anything else: a CRITICAL/VERY_HIGH rug-risk tier,
or a recent news item classified as hack/exploit/rug_allegations/
security_incident, immediately produces `AVOID` with `override_triggered
= True` - regardless of how bullish any other signal looks. This is
intentional, not a bug: see ADR-032 for why overrides are checked before
(not blended with) other signals.

## Known limitation

The entry ML model (Phase 6) is NOT currently wired into this
recommendation - that model's own metrics showed it performing worse
than random (ROC-AUC 0.24) on the small dataset it was trained on.
Passing it through as `None` correctly reflects "not used" rather than
trusting a known-unreliable signal.

**This has a real, significant consequence, stated precisely rather than
approximately**: with `entry_model_probability` always `None`, tracing
through `decision_logic.py`'s logic shows `BUY` is currently
UNREACHABLE through this runner - only `AVOID` (via an override) or
`HOLD` (everything else) can actually be produced, confirmed by direct
testing, not just read from the code. This is an honest, real
limitation of this first slice, not a subtle edge case - the decision
engine is currently a "does anything look actively wrong?" filter, not
yet a genuine buy signal generator. Wiring in a real, better-trained
entry model (once Phase 6's models are retrained on more/better data)
is what would make `BUY` reachable, and only requires changing the one
hardcoded `None` in `run_decision_check.py`.
