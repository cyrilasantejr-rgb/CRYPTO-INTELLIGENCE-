# Architecture Decision Records

Format: each decision states the context, the decision, and the tradeoff accepted.

---

## ADR-001: Separate batch and real-time ingestion paths

**Context**: The system needs both historical backfill/training data and low-latency
live signals.

**Decision**: Airflow owns batch orchestration exclusively. Kafka + stream processors
own real-time exclusively. They converge only at the lakehouse/feature layer.

**Tradeoff**: More moving parts than a single pipeline, but avoids batch tooling
becoming a real-time bottleneck and vice versa. This mirrors how most real trading /
fraud-detection systems are actually built.

---

## ADR-002: Medallion (Bronze/Silver/Gold) lakehouse architecture

**Context**: Raw vendor data is messy, inconsistent, and sometimes wrong. ML models
need clean, stable features.

**Decision**: Three layers — Bronze (raw, immutable, append-only), Silver (typed,
deduplicated, quarantine-on-failure), Gold (ML/analytics-ready feature tables).

**Tradeoff**: More storage and more pipeline stages than writing directly to one
table, but enables reprocessing (Silver/Gold can be rebuilt from Bronze if a
transformation bug is found), auditability, and independent evolution of each layer's
schema.

---

## ADR-003: Kafka topics partitioned by `token_address`

**Context**: Kafka only guarantees ordering within a partition, not across partitions.

**Decision**: Partition every real-time topic by `token_address`.

**Tradeoff**: A single very high-volume token could create a partition hotspot, but
correctness (per-token event ordering, e.g. liquidity-removal before price-crash for
the same token) matters more than perfectly even load at this project's scale.

---

## ADR-004: Deterministic `event_id` hash instead of vendor IDs

**Context**: Not all vendors provide stable IDs; webhook redelivery is common.

**Decision**: Compute `event_id = sha256(source + token_address + event_timestamp +
payload_hash)` at ingestion time and use it as the sole dedup key downstream.

**Tradeoff**: Slightly more compute at ingestion, but makes Silver-layer
deduplication uniform regardless of source reliability, and makes consumers
idempotent by construction.

---

## ADR-005: Entry model and exit model remain fully separate models

**Context**: "Should I enter?" and "should I hold/exit an open position?" are
different questions with different inputs (the exit model needs position-aware
inputs like unrealized P&L and time-in-trade that don't exist pre-entry).

**Decision**: Two independently trained, independently versioned models rather than
one model with a "position" flag feature.

**Tradeoff**: More models to maintain and retrain, but avoids a single model
conflating two different prediction tasks, and lets each be backtested and evaluated
against metrics appropriate to its actual job.

---

## ADR-006: Signal generation is architecturally separate from execution

**Context**: The system generates recommendations. Live-money execution is explicitly
out of scope for the initial build.

**Decision**: Decision engine output is a signal, consumed by a paper-trading engine.
No component has the ability to place a real trade in this phase.

**Tradeoff**: None significant — this is close to free architecturally (a clean
interface boundary) and removes an entire category of risk (unsupervised real-money
execution) from the initial system.

---

## ADR-007: Local dev uses Redpanda + MinIO instead of real Kafka + S3

**Context**: This is a cost-conscious personal project; local dev should be free and
fast to iterate on.

**Decision**: Local Docker Compose uses Redpanda (Kafka-API-compatible) and MinIO
(S3-API-compatible). Producer/consumer and boto3 code is written once against these
APIs and points at real Kafka/S3 in "production" via config/env, not code changes.

**Tradeoff**: Redpanda/MinIO aren't bit-for-bit identical to MSK/S3 in every edge
case, but the wire-protocol compatibility is close enough that this is a standard,
low-risk pattern for keeping dev costs at zero.

---

## ADR-008: Bronze S3 keys are run-scoped (append-only), not overwritten

**Context**: Phase 2 needed a key scheme for writing Parquet files to S3/MinIO
Bronze. Two options: a fixed deterministic key per (domain, date, token) that
gets overwritten on every run, or a key that includes a unique run id so
every ingestion run produces new files.

**Decision**: Keys include a run id:
`bronze/{domain}/dt={event_date}/token={token_address}/{run_id}.parquet`.
Re-running ingestion for an already-covered date/token writes an additional
file rather than replacing the existing one.

**Tradeoff**: More files accumulate in Bronze over time (storage cost, more
files for Silver to scan), but this is the only option consistent with
Bronze being genuinely immutable/append-only (see docs/architecture.md). A
fixed-key overwrite scheme risks a partial or buggy re-run silently
destroying previously-good raw data with no way to recover it - since Bronze
is our only copy of "what the vendor actually sent," that risk was judged
worse than the extra storage/file-count cost. Silver-layer dedup on
`event_id` (ADR-004) is what reconciles any duplicate candles across files.

---

## ADR-009: Local Spark stages Bronze/Silver via boto3 download/upload, not s3a://

**Context**: Phase 3 needed Spark to read Bronze data from MinIO and write
Silver data back to it. Spark's native S3 access uses Hadoop's S3A
connector, which requires exactly-matched JAR versions (hadoop-common,
hadoop-aws, aws-java-sdk-bundle) - a well-documented source of hours-long
debugging even for experienced engineers, due to version mismatches.

**Decision**: For local development, the Silver job downloads Bronze
Parquet files from MinIO using the already-tested `ObjectStoreClient`
(boto3), runs Spark against local disk, then uploads Silver/quarantine
output the same way. Spark itself never talks to S3/MinIO directly in
local dev.

**Tradeoff**: This adds a download/upload step that "real" S3-native Spark
wouldn't need, and doesn't exercise Spark's actual S3 connector. But real
Databricks (unlike local PySpark) has S3 access built in natively with no
JAR configuration required at all - so this local-staging approach is
purely a local-dev convenience. The transformation logic itself
(flatten_and_validate in databricks/silver/market_silver.py) is identical
either way and requires zero changes to run on real Databricks against
real S3.

---

## ADR-010: Downloading Spark-partitioned data must preserve folder structure

**Context**: Phase 4's feature job downloads Silver Parquet from MinIO the
same way Phase 3 downloads Bronze - or so it seemed. Running it for real
against actual Silver data raised `UNRESOLVED_COLUMN: token_address`, even
though the Silver write in Phase 3 clearly includes that column.

**Root cause**: `df.write.partitionBy("token_address", "event_date")`
does NOT store those columns inside the Parquet files - Spark encodes them
entirely in the directory structure instead
(`token_address=X/event_date=Y/part-....parquet`), and reconstructs them
as columns only when reading the partitioned directory tree as a whole
("Hive-style partition discovery"). Bronze never hit this because Bronze
is written with plain boto3/pyarrow, where token_address genuinely is a
column inside the file - Silver and Gold, written by Spark's
`partitionBy`, are fundamentally different on this point.

The original download helper flattened every downloaded file into one
directory with renamed filenames (`file_0.parquet`, `file_1.parquet`...),
which silently discarded the partition folder names Spark needed to
reconstruct `token_address`/`event_date`.

**Decision**: the download helper preserves each object's relative path
below its prefix, recreating the same `token_address=X/event_date=Y/`
folder structure locally before Spark reads it.

**Tradeoff**: none really - this is strictly a bug fix, not a design
tradeoff. Worth documenting anyway because it's a genuinely common
Spark gotcha: partition columns live in the path, not the file, and any
code that moves partitioned Parquet files around (not just our MinIO
staging step) needs to either preserve that path structure or explicitly
re-derive the partition columns some other way.
---

## ADR-011: Backtesting engine uses pandas, not Spark

**Context**: Phase 5 needed a backtesting engine that computes a
compounding equity curve from Gold features and per-row trading signals.

**Decision**: Implemented entirely in pandas/numpy - no Spark session,
no distributed computation.

**Tradeoff**: An equity curve is inherently sequential (today's capital
depends on yesterday's capital), the same recursive shape that already
forced Phase 4's EMA/MACD out of Spark's window functions and into a
pandas UDF. A backtest runs against an already-aggregated per-token time
series small enough to comfortably fit in memory - real-world quant
backtesting libraries (backtrader, vectorbt, zipline) all operate on
pandas/numpy for exactly this reason. Using Spark here would add
complexity (session startup, distributed overhead) with no actual
payoff; the test suite makes this concrete - the backtest engine's full
test file runs in well under a second, versus 30-45 seconds for any
Spark-based test file in this repo, because there's no JVM to start.
---

## ADR-012: Phase 6 exit model uses simulated positions, not real ones

**Context**: The exit model's spec calls for position-aware inputs -
entry price, unrealized P&L, time-in-trade, highest-price-since-entry.
Real position tracking doesn't exist until Phase 13 (paper trading
engine).

**Decision**: Phase 6's exit model approximates position-aware features
by assuming a fixed hypothetical holding period (e.g. "assume entry was
4 hours ago") computed directly from market data, rather than building
a throwaway position tracker just to unblock this phase.

**Tradeoff**: The resulting "unrealized_return", "periods_held", and
"drawdown_from_high" features are approximations, not real position
state - genuinely different numbers than what Phase 13's real position
manager will eventually produce. This is stated explicitly in
ml/exit/train.py's module docstring rather than left implicit. The
training/evaluation code (train_exit_model) only depends on column
names, not on where those columns came from - when Phase 13 lands,
`add_simulated_position_features` gets replaced by a real position-data
join, and no other exit model code needs to change.

---

## ADR-013: Entry/exit label thresholds tuned to observed volatility; training guards against single-class data

**Context**: The first real run of Phase 6 training (7 days of hourly SOL
data) crashed with `ValueError: This solver needs samples of at least 2
classes`. Root cause: the original entry-model thresholds (3% up before
2% down, within 6 hours) were picked without reference to the actual
data - the real dataset's total peak-to-trough range across the entire
week was only ~6.7%, making a 3%-within-6-hours move essentially
unreachable. Every label came back 0, and scikit-learn's
LogisticRegression has no valid decision boundary to fit with only one
class present.

**Decision**: Two separate fixes, addressing two separate problems:
1. Lowered `ENTRY_UPPER_PCT`/`ENTRY_LOWER_PCT` to 1%/1% and
   `EXIT_DECLINE_THRESHOLD` to 1%, based on the token's actual observed
   volatility rather than an arbitrary guess.
2. Added an explicit class-balance check in `ml/run_training.py` before
   calling either training function - if a training split ends up with
   only one label class (which will happen again with a different
   token, a calmer week, or tighter thresholds), the run logs a clear,
   actionable warning and skips that token/model rather than crashing
   the entire training run.

**Tradeoff**: 1%/1% thresholds mean the entry model is now predicting a
smaller, more frequent move rather than a rarer, larger one - a
legitimate modeling choice with its own tradeoffs (more trade
opportunities, smaller edge per trade, more sensitive to trading costs
from the Phase 5 backtest engine) rather than a strictly "better" number.
The class-balance guard doesn't fix bad thresholds - it just stops a bad
threshold-and-data combination from taking down the whole training run,
consistent with the project's established quarantine/skip-and-log
philosophy (Silver's data-quality quarantine, Bronze's per-record error
isolation) applied here to model training instead of row validation.

---

## ADR-014: Airflow runs in its own venv; tasks shell out to the project venv

**Context**: Phase 7 needed to orchestrate the existing pipeline scripts
(ingestion, Silver, Gold features, backtest, training) as a scheduled
Airflow DAG.

**Decision**: Airflow is installed in a completely separate Python virtual
environment from the project's own venv. Every DAG task is a
`BashOperator` that invokes the project venv's Python interpreter as a
subprocess to run the actual pipeline script - Airflow itself never
imports any project code (`ingestion/`, `databricks/`, `features/`,
`backtesting/`, `ml/`) directly.

**Tradeoff**: This is one more environment to set up and keep track of,
and BashOperator subprocess calls are slightly less "native" than calling
Python functions directly via a PythonOperator. But Airflow's own pip
install pins specific versions of common libraries (SQLAlchemy, Pydantic,
etc.) via its constraints mechanism, which risks silently downgrading or
conflicting with the pipeline's own dependencies if installed into the
same environment - a real, well-documented source of breakage. Separating
orchestrator environment from execution environment also mirrors how a
real deployment would actually work (an Airflow worker triggering a
separate containerized/venv'd task), so this isn't just a local-dev
workaround - it's the more correct pattern generally.

**Scope note**: the DAG hardcodes a single token via an Airflow Variable
rather than dynamically generating one task per token in a watchlist.
A real watchlist-driven version would use Airflow's dynamic task mapping
(`.expand()`) to generate one ingestion task per token from a config-
or database-driven list - deferred as a clear, named future improvement
rather than silently scoped out.
