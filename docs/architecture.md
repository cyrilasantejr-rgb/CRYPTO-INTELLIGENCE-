# Architecture

## Purpose

Crypto Intelligence & Risk Platform — an end-to-end data engineering / ML engineering
system for researching and paper-trading Solana tokens and memecoins. The system
generates probabilistic, explainable recommendations (BUY / ADD / HOLD / TAKE PARTIAL
PROFIT / REDUCE / EXIT / EMERGENCY EXIT / AVOID). It never claims certainty about
future price movement, and signal generation is architecturally separated from trade
execution (paper trading only, initially).

## High-level flow

```
Data sources (market/RPC/security/social/news)
        │
        ├── Batch path: Airflow → S3 Bronze → Databricks/PySpark → Silver → Gold
        │
        └── Real-time path: WebSocket/webhook → Kafka → stream processor →
                             feature calc → risk/security eval → ML inference
                                       │
                                       ▼
                              Decision Engine
                                       │
                                       ▼
                Position Manager / Alert Engine / Paper Trading
                                       │
                                       ▼
                           Postgres + Redis (serving)
                                       │
                                       ▼
                          FastAPI → Streamlit dashboard
```

Batch and real-time ingestion are intentionally separate paths that converge at the
lakehouse and feature layer. Airflow handles batch orchestration only; it never
processes real-time events. Kafka/stream processing handles low-latency events only;
it never runs historical backfills.

## Component responsibilities

| Component | Responsibility | Explicitly NOT responsible for |
|---|---|---|
| Ingestion adapters | One adapter per vendor, normalized to a common `SourceAdapter` interface | Business logic, feature calculation |
| Kafka | Durable, ordered event bus | Long-term storage |
| Stream processor | Real-time feature calc, security rule checks | Model training |
| Airflow | Batch scheduling/orchestration | Heavy compute (delegates to Databricks) |
| Databricks/PySpark | Bronze→Silver→Gold transforms at scale | Real-time serving |
| Gold tables | Canonical ML-ready features (feature store) | Raw storage |
| ML models (entry/exit/rug/social/news) | Independent, versioned predictions | Final decision-making |
| Decision engine | Combines model outputs + deterministic overrides into one action | Placing trades |
| Position manager | P&L, stop levels, staged profit-taking | Signal generation |
| Paper trading engine | Simulated execution | Real execution (out of scope initially) |
| Alert engine | Severity-tagged event notifications | Decision-making |
| Postgres | Operational state (positions, alerts) | Analytics at scale |
| Redis | Low-latency cache of latest features/prices | Durable storage |
| FastAPI | Serving API | ML training |
| Streamlit | Human-facing dashboard | Business logic |

## Repository structure

See top-level directory layout in this repo. Key additions beyond a typical layout:
- `common/interfaces/` — the abstract `SourceAdapter` base class every vendor
  integration implements, so vendors can be swapped without touching downstream code.
- `common/schemas/` — shared Pydantic models for the Bronze event envelope.

## Bronze event envelope

Every bronze-layer event, regardless of domain, shares this shape:

```json
{
  "event_id": "sha256(source + token_address + event_timestamp + payload_hash)",
  "source": "helius | birdeye | goplus | ...",
  "schema_version": "1.0",
  "ingestion_timestamp": "ISO8601 - when we received it",
  "event_timestamp": "ISO8601 - when it happened upstream",
  "token_address": "partition key",
  "domain": "market | transaction | wallet | holder | security | news | social",
  "payload": { "...source-specific raw JSON..." }
}
```

`event_id` is a deterministic hash rather than a vendor-supplied ID, because not every
vendor provides a stable ID and webhook redelivery is common. This lets Silver-layer
deduplication work uniformly regardless of source reliability.

## Kafka topic design

Naming convention: `{domain}.{entity}.{event}.v{schema_version}`, partitioned by
`token_address` so all events for a given token preserve relative order (needed e.g.
for security overrides where a liquidity-removal event must be processed before a
correlated price event for the same token).

| Topic | Partition key | Purpose |
|---|---|---|
| `market.price.raw.v1` | token_address | tick-level price updates |
| `market.trade.raw.v1` | token_address | individual buy/sell trades |
| `onchain.liquidity.event.v1` | token_address | LP add/remove |
| `onchain.wallet.transfer.v1` | token_address | transfers involving watched wallets |
| `onchain.holder.change.v1` | token_address | holder count/concentration changes |
| `security.alert.raw.v1` | token_address | GoPlus / rule-engine findings |
| `social.mention.raw.v1` | token_address | raw social mentions |
| `news.article.raw.v1` | token_address or `_unclassified_` | raw news pre-entity-extraction |
| `signals.decision.v1` | token_address | decision engine outputs |
| `alerts.triggered.v1` | token_address | fired alerts |

## Bronze / Silver / Gold

- **Bronze**: append-only, one table per domain, Parquet/Delta, partitioned by
  `ingestion_timestamp` date. Envelope schema verbatim, nothing transformed or dropped.
- **Silver**: one clean table per domain. Deduplicated on `event_id`, typed, timestamps
  normalized to UTC. Invalid records quarantined, not deleted. Partitioned by
  `token_address` + date.
- **Gold**: analytics/ML-ready feature tables — `token_market_features`,
  `onchain_features`, `wallet_features`, `holder_features`, `liquidity_features`,
  `security_features`, `social_features`, `news_features`, `position_features`,
  `trading_signals`, `model_performance`. Partitioned by `token_address` + date.

## Local development architecture (target cost: $0)

Docker Compose services (introduced progressively across phases, not all at once):
- **Postgres** — operational DB
- **Redis** — cache
- **Redpanda** — Kafka-API-compatible, single binary, lighter than Kafka+Zookeeper
  for local dev. Production could swap in real Kafka/MSK without changing
  producer/consumer code since the wire protocol matches.
- **MinIO** — S3-compatible local object storage so ingestion code is written once
  against boto3 and works against both MinIO (dev) and real S3 (prod).
- **Airflow** — LocalExecutor via docker-compose.
- **PySpark** — installed locally via pip for Bronze→Silver→Gold development;
  Databricks Community Edition used later for Databricks-specific concepts.
- **FastAPI** + **Streamlit** — application containers.

## Cloud (AWS) architecture — cost-aware

| Need | Production option | Lower-cost alternative used here |
|---|---|---|
| Object storage | S3 | S3 (already cheap — used directly) |
| Spark compute | Databricks paid workspace | Databricks Community Edition / EMR Serverless |
| Orchestration | MWAA (managed Airflow) | Self-hosted Airflow, single small EC2/Fargate task |
| Streaming | MSK (managed Kafka) | Redpanda Cloud free tier / self-hosted Redpanda |
| Relational DB | RDS Postgres | Supabase free tier / local Postgres for dev |
| Cache | ElastiCache Redis | Upstash Redis free tier |
| API hosting | ECS Fargate | Single small EC2 / Render / Fly.io free tier |
| Secrets | AWS Secrets Manager | SSM Parameter Store + local `.env` |

## Security / secrets

- `.env.example` committed with placeholder values; `.env` gitignored.
- Local AWS access via `~/.aws/credentials` profile — never hardcoded, never committed.
- Production: IAM roles scoped to specific S3 prefixes (e.g. ingestion role can write
  to `bronze/` only, never `gold/`).
- Structured logging redacts any field matching `*key*`, `*token*`, `*secret*`.
- 401/403 from an external API is treated as a hard stop, never retried.

## Testing strategy

- **pytest** across the board.
- `tests/unit/` — pure functions, no I/O (indicators, feature math, decision logic).
- `tests/integration/` — adapters against mocked/sandboxed APIs, Kafka round-trip tests.
- `tests/data_quality/` — schema/null/range validation (Pandera or Great Expectations,
  introduced in Phase 2/3).
- GitHub Actions CI runs pytest + `ruff` + `black --check` on every PR.

## Non-goals (explicit)

- No live-money execution in the initial system — paper trading only.
- No claim of certainty about future price movement anywhere in the system.
- No single security API or single ML model treated as ground truth — the decision
  engine combines multiple signals and applies deterministic overrides.
