# Data Dictionary

This document is built up progressively as each domain is implemented. Phase 0
defines the shared envelope and stubs out the domains to come.

## Bronze envelope (applies to every domain)

| Field | Type | Description |
|---|---|---|
| `event_id` | string | Deterministic dedup key: sha256(source + token_address + event_timestamp + payload_hash) |
| `source` | string | Vendor name, e.g. `helius`, `birdeye`, `goplus` |
| `schema_version` | string | Envelope/payload schema version, e.g. `1.0` |
| `ingestion_timestamp` | timestamp (UTC) | When our system received the event |
| `event_timestamp` | timestamp (UTC) | When the event occurred upstream |
| `token_address` | string | Solana mint address; Kafka partition key |
| `domain` | string | One of: market, transaction, wallet, holder, security, news, social |
| `payload` | JSON | Raw, unmodified source-specific payload |

## S3/MinIO Bronze partitioning scheme (Phase 2)

```
bronze/{domain}/dt={event_date}/token={token_address}/{run_id}.parquet
```

- `dt` is the UTC date of `event_timestamp` (when the event happened), not
  `ingestion_timestamp` (when we received it) - queries filter by when
  things happened, so that's what should drive partition pruning.
- `run_id` makes writes append-only: re-running ingestion for an
  already-covered date/token adds a new file rather than overwriting the
  prior one. See ADR-008.

## Domains (schemas to be filled in as each is implemented)

- `market` — price ticks, OHLCV, trades — **Phase 1**
- `transaction` — on-chain transactions — Phase 9
- `wallet` — wallet-level activity — Phase 9
- `holder` — holder count/concentration snapshots — Phase 9
- `security` — GoPlus/rule-engine findings — Phase 10
- `news` — raw news articles — Phase 11
- `social` — raw social mentions — Phase 11

## Gold feature tables (to be filled in per phase)

- `token_market_features` — Phase 4
- `onchain_features`, `wallet_features`, `holder_features`, `liquidity_features` — Phase 9
- `security_features` — Phase 10
- `social_features`, `news_features` — Phase 11
- `position_features`, `trading_signals` — Phase 12/13
- `model_performance` — Phase 14
