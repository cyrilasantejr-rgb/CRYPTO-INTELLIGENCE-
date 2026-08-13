# Phase 9 (continued): Dev-Wallet Outflow Monitoring

Detects whether a monitored wallet (e.g. a token's creator/dev wallet)
has been sending a specific token elsewhere - a real rug-pull warning
sign. Uses Helius, a new provider for this project (Birdeye doesn't
expose raw wallet transaction history).

## One-time setup

1. Sign up at **dashboard.helius.dev** (free, no card required)
2. Copy your API key from the dashboard
3. Add it to `.env`:

```
HELIUS_API_KEY=your_key_here
```

## Running it

```
source venv/bin/activate
python3 -m wallet_intelligence.run_dev_wallet_monitor --wallet <wallet_address> --token <token_mint_address>
```

Prints how many transactions were examined, how many were outflows of
the specific monitored token, the total amount sent out, and flags a
warning if any outflow happened within the last 24 hours.

## Finding a wallet to test with

Wrapped SOL (the token used throughout this project) doesn't have a
meaningful "dev wallet" - it's a canonical wrapped asset, not a
memecoin with a creator. To test this meaningfully, use:
- Your own wallet address and a token you actually hold, to sanity-check
  the mechanism against real, known activity
- Or a real memecoin's creator wallet address (findable via Birdeye's
  token creation info, or a Solana block explorer like Solscan)

## Known limitation

Same situation as every other Phase 9 piece tonight: this is the first
real test of this exact Helius response shape - the field names used in
parsing (`tokenTransfers`, `fromUserAccount`, `mint`, `tokenAmount`) are
based on Helius's documented schema, not a live-verified response. If
the actual response differs, expect a similar quick field-name fix to
what happened with the Birdeye holder endpoint tonight.

Also scope-limited (see ADR-025): this detects raw outflows, not
WHETHER those outflows are a DEX sale, a transfer to another wallet the
same person controls, or a deposit to an exchange - those all look the
same from this wallet's side. Distinguishing them is real future work,
not built yet.
