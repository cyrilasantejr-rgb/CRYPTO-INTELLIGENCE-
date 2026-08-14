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

---

## ADR-015: Airflow 2.11.0, not 3.x - concrete failure, not a preference

**Context**: Phase 7 was initially built against Airflow 3.3.0 (the
current stable release at the time). DAG parsing succeeded, the DAG
loaded correctly in the UI with the right 5-task structure, and it
appeared ready to run - but the first real triggered run failed on the
very first task with `ModuleNotFoundError: No module named 'airflow.sdk'`,
raised from inside Airflow's own internal task-launching mechanism,
before our BashOperator's command ever executed.

**Investigation**: Airflow 3.x introduced a new Task Execution API/Task
SDK architecture, where each task runs via a supervisor process that
itself depends on importing `airflow.sdk` to communicate with Airflow's
API server. This is architecturally more complex than Airflow 2.x's
model, and the failure occurred in that supervisor layer rather than in
anything our DAG file does. Rather than trial-and-error against a system
with unfamiliar internals, this was tested directly: a completely
separate, throwaway Airflow install was set up specifically to isolate
the cause.

**Decision**: downgrade to Airflow 2.11.0, using the classic, long-stable
DAG API (`from airflow import DAG`, `from airflow.operators.bash import
BashOperator`) instead of the newer `airflow.sdk` module. This avoids the
Task Execution API entirely.

**Verification, not assumption**: before changing anything in the
project, this fix was proven in isolation:
1. Installed Airflow 2.11.0 in a throwaway venv.
2. Loaded the (rewritten) DAG file through Airflow's actual `DagBag`
   loader (the same mechanism the real scheduler uses) - zero import
   errors, exact same 5-task structure as before.
3. Ran `airflow tasks test crypto_intelligence_pipeline
   ingest_market_data <date>` against the real DAG file - the task
   executed the BashOperator's command and returned `Command exited
   with return code 0` / `Marking task as SUCCESS`, with no trace of
   the earlier `airflow.sdk` error.

Only after that verification did the fix get applied to the actual
project DAG file.

**Tradeoff**: Airflow 2.11.0 doesn't have Airflow 3's newer features
(the redesigned UI, the Task Execution API's stronger process isolation,
etc.), but for a single-node local-dev setup orchestrating a handful of
BashOperator tasks, none of those matter, and 2.x's simpler, extremely
well-documented architecture is a better fit than debugging a newer
system's internal supervisor process on a fresh local install.

---

## ADR-016: `execute_tasks_new_python_interpreter=True` to fix macOS CPU-spin

**Context**: After fixing the Airflow 3.x incompatibility (ADR-015) and
the gunicorn `SIGSEGV` crash loop (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY`),
a real triggered DAG run still failed to progress: the `ingest_market_data`
task sat in `running` state indefinitely, and its underlying process
consumed 100% CPU continuously for 7+ minutes (confirmed via `ps aux` -
real CPU time accumulating, not a blocked/waiting process at ~0% CPU).

**Investigation**: this matches a documented, known macOS + Python issue
(not specific to this project) where Airflow's `fork()`-based task
supervisor launching interacts badly with proxy-detection threading
behavior on macOS, causing a busy-wait/livelock rather than a clean crash
or completion. Multiple independent Airflow GitHub discussions describe
the identical symptom and the identical fix.

**Decision**: set `AIRFLOW__CORE__EXECUTE_TASKS_NEW_PYTHON_INTERPRETER=True`,
Airflow's own official configuration for avoiding `fork()` when launching
task supervisor processes - it spawns a fresh subprocess instead, which
sidesteps the entire class of macOS fork-related issues at the root,
rather than continuing to patch around individual symptoms.

Also switched local dev from `airflow standalone` (bundles scheduler +
gunicorn webserver into one process) to running the scheduler and
webserver as separate processes. The scheduler alone - which is what
actually executes the pipeline - proved reliable once both fork-related
fixes were applied; the gunicorn-based webserver remains a known weaker
point on macOS even with fixes applied, so it's now treated as optional
(CLI-based triggering/monitoring via `airflow dags trigger` and
`airflow tasks states-for-dag-run` doesn't depend on it at all).

**Tradeoff**: `execute_tasks_new_python_interpreter=True` is measurably
slower per task (Airflow has to reload its own dependencies in a fresh
interpreter for every task, rather than reusing a forked copy), but for
a personal project with a handful of daily-scheduled tasks, correctness
and stability matter far more than shaving milliseconds off task startup.
This setting would very likely be unnecessary on a real Linux production
deployment (MWAA, a Linux VM, Docker) - it's a macOS-local-dev-specific
tradeoff, worth revisiting if this project ever moves to a Linux host.

---

## ADR-018: `PYSPARK_PYTHON` must be set explicitly for launchd-run jobs

**Context**: Running `scripts/run_daily_pipeline.sh` interactively worked
fine, but the automated version failed at the Silver/feature-engineering
stages with `ModuleNotFoundError: No module named 'pandas'` - inside a
PySpark worker process, despite the project's venv clearly having pandas
installed (proven working in every prior interactive run).

**Root cause**: PySpark spawns separate worker subprocesses for
distributed execution (this happens even in local mode). By default,
each worker resolves its own `python3` by searching `PATH` at spawn
time - it does NOT automatically reuse the exact interpreter that
launched the main script. In an interactive terminal this typically
works by coincidence (the venv's `bin/` directory is first on `PATH`),
but launchd runs jobs with a minimal, largely empty `PATH`, so the
worker silently fell back to a system Python without pandas installed.

**Verification before applying the fix**: reproduced this exact failure
in isolation - ran a minimal PySpark `applyInPandas` job with a
deliberately broken `PATH` (no venv, no pandas) and confirmed it failed
with the identical error. Then set `PYSPARK_PYTHON` to an explicit
interpreter path with the same broken `PATH` otherwise unchanged, and
confirmed the job succeeded. This is a controlled before/after test, not
a guessed fix.

**Decision**: `run_daily_pipeline.sh` now explicitly exports
`PYSPARK_PYTHON="$VENV_PYTHON"` before any Spark-based stage runs. This
forces every Spark worker subprocess to use the exact same interpreter as
the driver, regardless of what `PATH` looks like in the calling
environment.

**Tradeoff**: none really - this is strictly a correctness fix for
running PySpark jobs in any minimal-environment context (launchd, cron,
CI, a bare Docker container), not just this project's specific setup.
Worth setting as standard practice any time PySpark is invoked outside
an interactive shell.

---

## ADR-019: Polling instead of WebSocket - Birdeye's free tier has no WS access

**Context**: Phase 8 needed a real-time (or near-real-time) price data
source. WebSocket access was assumed available on the free tier already
in use since Phase 1.

**Decision**: checked Birdeye's actual pricing page before building
anything - WebSocket access requires the **Premium tier ($199/month)**
or higher. The free **Standard** tier (used throughout this project,
$0/month) has zero WebSocket access. Rather than silently switching to a
paid tier or building something that would fail on this project's actual
account, Phase 8 uses fast polling (`/defi/multi_price`, available on the
free tier) instead of a WebSocket subscription.

**Tradeoff**: polling introduces genuine latency (our default: 20
seconds) that a WebSocket push wouldn't have - this is NOT true real-time.
For a personal memecoin-watchlist tool, the difference between "instant"
and "20 seconds old" is not meaningfully different in practice, and this
keeps the project's cost at $0. If genuine sub-second latency ever
becomes a real requirement, upgrading to Premium is a known, isolated
change - only `ingestion/market/birdeye_realtime_adapter.py` would need
to change (implement a WebSocket-based adapter behind the same
`RealtimePriceAdapter` interface); the Kafka producer, consumer, and
alerting logic would all be unaffected, since they only depend on the
interface, not the transport.

---

## ADR-020: In-memory alert state, not Redis, for the first pass

**Context**: The stream consumer needs to remember recent price history
per token to detect meaningful moves (a rolling window). Redis was
planned in the original architecture as the caching/operational-state
layer.

**Decision**: `PriceHistoryTracker` holds this state in a plain Python
dict in memory, not Redis, for this first implementation of Phase 8.

**Tradeoff**: alert history resets to empty if the consumer process
restarts - a real limitation for a genuinely long-running production
service. But introducing Redis is a separate, real piece of
infrastructure (connection handling, serialization format, TTL/eviction
policy, a new docker-compose service) that deserves its own deliberate
design rather than being bolted on as an afterthought here. Deferred as
a clear, named next step rather than silently worked around - the
`PriceHistoryTracker` class's interface (`update(token, price,
timestamp) -> Alert | None`) was deliberately kept storage-agnostic, so
swapping its internal dict for a Redis-backed store later would not
require changing anything that calls it.

---

## ADR-021: `/defi/price` (single-token), not `/defi/multi_price`, on the free tier

**Context**: Phase 8's realtime adapter was originally built against
Birdeye's `/defi/multi_price` endpoint, expecting it to be available on
the same free Standard tier used throughout this project. Running it for
real against the actual API produced a `401` auth error - on the exact
same API key that has worked for every other endpoint used so far.

**Investigation**: fetched Birdeye's actual "Data Accessibility by
Packages" table rather than guessing. It confirms `/defi/multi_price`
requires the **Lite tier ($39/mo)** or above; the free **Standard** tier
has no access to it at all. The same table confirms `/defi/price`
(single-token) IS available on Standard - the same endpoint shape
already used successfully by the historical OHLCV work since Phase 1.

**Decision**: `BirdeyeRealtimePriceAdapter` now calls `/defi/price` once
per token in the watchlist, instead of batching the whole watchlist into
one `/defi/multi_price` call.

**Tradeoff, stated plainly**: this means N tokens in the watchlist means
N API calls per poll, not 1 - a real cost that scales linearly instead of
staying flat. For the current 1-token watchlist this is a non-issue, and
even a watchlist of a dozen tokens stays comfortably within the free
tier's 1 request/second limit at a 20-second poll interval. If the
watchlist grows large enough for this to become a genuine constraint,
that's the concrete, measurable signal to actually pay for Lite/Premium
- not something to keep working around on the free tier indefinitely.

**Process note**: this is the second time in this project a Birdeye
tier-access assumption turned out to be wrong when checked against real
usage (the first being WebSocket access, ADR-019). Both times the fix
was found by checking Birdeye's actual, current documentation rather
than assuming based on what "should" be available on a free tier - worth
remembering as a standing practice for any future vendor integration.

---

## ADR-022: Herfindahl-Hirschman Index alongside top-10% concentration

**Context**: Phase 9's holder-concentration analysis needed a way to
quantify rug-pull-adjacent risk from holder distribution data.

**Decision**: compute both `top10_concentration_pct` (the standard,
easy-to-explain "do 10 wallets own most of the supply?" number) AND the
Herfindahl-Hirschman Index (HHI - sum of squared ownership shares, a
standard concentration measure from economics/antitrust analysis),
rather than relying on top-10% alone.

**Why both, concretely**: two holder distributions can have identical
top10_concentration_pct but very different real risk. 10 wallets each
holding 3% (30% total) is meaningfully different from 1 wallet holding
21% and 9 wallets holding 1% each (also 30% total) - the second is a
single dominant whale, the first is a more genuinely distributed group.
top10% alone reports these as identical; HHI correctly reports the
second as more concentrated. Verified with a test constructing exactly
this pair of distributions and asserting HHI tells them apart.

**Decision, part two**: this module deliberately does NOT blend these
into one composite 0-100 risk score. That blending is Phase 10's
security/decision engine's job - it will combine this signal with
liquidity, mint authority, and other security signals using its own
explicit, documented logic. Doing that blending here, prematurely and
in isolation, would hide exactly the nuance (see the HHI example above)
that makes computing two separate metrics worthwhile in the first place.
Risk tiers ARE assigned here (LOW/MODERATE/HIGH/VERY_HIGH/CRITICAL,
directly off top10_concentration_pct), matching the 0-20/21-40/41-60/
61-80/81-100 RUG_RISK_SCORE scale from the project's original design -
kept on that same scale specifically so Phase 10 can reason about this
signal consistently with everything else, without another unit
conversion.

**Honest limitation**: like Phase 8's Kafka wiring, the adapter's real
API call has not been run against the live Birdeye endpoint by me before
handing this off - I don't have a route to birdeye.so from this sandbox.
Only the pure concentration-math logic (7 tests) and the adapter's
request-construction/retry logic (4 tests, mocked HTTP) have been
verified. The exact response field names (`items`, `ui_amount`,
`top10HoldPercent`) are based on Birdeye's published API reference, not
a live-tested response - this is the first real test of this exact
endpoint shape, on the actual Mac.

---

## ADR-023: Real Birdeye holder response uses snake_case, not camelCase

**Context**: ADR-022 flagged honestly that the holder adapter's field
names (`ui_amount`, `top10HoldPercent`) were based on Birdeye's written
API reference, not a live-tested response, since this sandbox can't
reach birdeye.so. Running it for real produced a working response with
real data (3.3 million SOL holders, top-10 concentration of 1.2% -
consistent with SOL being a widely-distributed base asset, not a
concentrated token) but the code reported "no holder amounts returned"
and dumped the raw payload instead of computing metrics.

**Root cause**: the real API response uses snake_case field names
(`amount`, `top10_hold_percent`) rather than the camelCase the written
docs implied (`ui_amount`, `top10HoldPercent`). The `items` wrapper key
and `owner` field were correctly guessed; only the amount and top-level
percentage field names were wrong.

**Decision**: fixed the parsing to use the real field names, confirmed
against the actual live response rather than the docs page.

**Process note**: this is now the third time in this project that a
vendor's actual behavior differed from its written documentation
(WebSocket tier access, multi_price tier access, and now this field-
naming mismatch). The pattern holding up across all three: code that
fails informatively (a clear warning + the raw payload printed) rather
than silently or by crashing made each of these fast to diagnose and
fix once actually run - worth continuing to write vendor-integration
code this way as standard practice, not just for Birdeye.

---

## ADR-024: Total-supply denominator, not top-100-sample denominator, for the risk verdict

**Context**: Running the holder analysis for real on SOL produced two
wildly disagreeing numbers: our own computed top-10 concentration said
83.5% (CRITICAL), while Birdeye's own reported `top10_hold_percent` said
1.2% (correctly LOW, matching reality - SOL is a massive, widely-
distributed base asset, not a concentrated token).

**Root cause**: `fetch_top_holders` only fetches the top 100 holders
(a vendor-imposed page size, not total supply). The original
`compute_concentration_metrics` computed "top 10 as a % of the SUM OF
THE FETCHED LIST" - i.e. top 10 as a share of just those 100 holders'
combined balance, not as a share of the token's actual total circulating
supply. With millions of real holders excluded from the sample, that
denominator is far too small, inflating the percentage by roughly 70x
in this case. Birdeye's own `top10_hold_percent` is correctly computed
against real total supply.

**Decision**: the actual risk verdict (`risk_tier` shown to the user)
now comes from Birdeye's own `top10_hold_percent`, run through the same
`classify_risk_tier()` function for consistent tier logic. The locally-
computed sample-relative metrics (top10% and HHI among the fetched top
100) are kept and still shown, but explicitly relabeled as a
supplementary "concentration among the sampled whales themselves"
signal - a legitimate, different, and still useful number (it answers
"even among the biggest holders, is ownership itself lopsided?"), just
not comparable to a total-supply-based percentage and not what drives
the risk tier.

**Why this matters more than a typical bug**: this is a security/risk-
assessment tool. A wrong "CRITICAL" verdict on a token that's actually
fine is exactly the kind of false alarm that erodes trust in the whole
system (or worse, a wrong "LOW" on something genuinely dangerous). Caught
here because the real output was checked against real-world intuition
(SOL should not show as critically concentrated) rather than just
checking that the code ran without crashing - a reminder that "the
script runs and prints a number" and "the number is correct" are
different bars, especially for anything feeding into risk decisions.

---

## ADR-025: Dev-wallet outflow detection - scope and honest limitations

**Context**: Phase 9's remaining piece needed a way to detect whether a
token's dev/creator wallet (or any monitored wallet) is selling off or
moving out their holdings - a well-known rug-pull warning sign.

**Provider decision**: Helius (dashboard.helius.dev), not Birdeye -
Birdeye's wallet endpoints are aggregated PnL/net-worth summaries, not
raw transaction history. Checked Helius's actual pricing/access before
building (same discipline as ADR-019/021 after getting burned by
assuming Birdeye tier access): free tier is 1M credits/month, no card
required, and covers the Enhanced Transactions endpoint used here.

**Scope decision, stated plainly**: this detects raw token OUTFLOWS
from a wallet - transfers where the monitored wallet is the sender. It
deliberately does NOT yet distinguish "sold on a DEX" from "transferred
to another wallet they also control" from "sent to a known exchange" -
all three look identical from this wallet's perspective (token left),
but mean different things. That distinction needs either DEX-swap-
specific transaction-type parsing or wallet-clustering analysis (tracing
where the money goes next) - both genuinely separate pieces of future
work, not silently skipped, just not built in this slice. What's built
answers the narrower, still genuinely useful question: "is this token
leaving this wallet, how much, and how recently."

**Honest limitation, same as every other Phase 9 piece tonight**: the
Helius adapter's field names (`tokenTransfers`, `fromUserAccount`,
`mint`, `tokenAmount`, `timestamp`) are based on Helius's documented
schema, not yet verified against a live response - this sandbox has no
route to Helius either. Given tonight's pattern (Birdeye's real
responses differed from docs in real, if minor, ways three separate
times), some field-name adjustment after the first live run should be
expected here too, not treated as a surprise if it happens.

**What IS thoroughly verified**: the pure outflow-detection logic (9
tests covering incoming-vs-outgoing correctly excluded, unrelated
tokens correctly ignored, multi-transaction summing, recency-window
correctness, and graceful handling of missing/malformed transaction
data) and the adapter's request/retry construction (4 tests, mocked
HTTP).

---

## ADR-026: Phase 10 security engine - composite scoring, uncertain field names handled defensively

**Context**: Phase 10 needed to combine the signals built across Phase 9
(holder concentration, dev-wallet outflow detection) with new token
security metadata (mint/freeze authority) into one final RUG_RISK_SCORE,
per the project's original design.

**Composite scoring decision**: implemented in
`rug_pull_intelligence/security_scoring.py` as a pure, fully-tested
function taking each signal as an explicit parameter (not fetched
internally), with three deliberate properties:
1. **Points are capped per-signal**, so no single red flag - even the
   worst possible value of one signal alone - can push an otherwise-
   clean token straight to CRITICAL. Reaching the highest tiers requires
   multiple independent signals agreeing something is wrong. Verified
   with a test asserting exactly this (`CRITICAL` holder concentration
   alone scores 50/100, not 100).
2. **A missing signal contributes zero points, never the worst case** -
   an API failure or a vendor field that couldn't be found must not be
   silently treated as "assume the worst," which would make the score
   unreliable in exactly the situations (partial data) where honesty
   about uncertainty matters most.
3. **Every point-contributing signal is named in a human-readable
   `reasons` list** - no hidden weighting a person reading the output
   can't audit, consistent with the project's "every recommendation
   must be explainable" requirement stated from the very start.

**Uncertain field names, handled upfront rather than discovered later**:
`/defi/token_security`'s exact response schema isn't confirmed - the
docs page renders its example response via JavaScript this project's
tooling can't execute, and given three separate real-vs-documented
Birdeye mismatches already tonight, there's no reason to assume this
endpoint is different. Rather than guess one field name and find out
it's wrong on the first live run (as happened with the holder endpoint),
`run_security_check.py` checks several plausible field name variants
for mint/freeze authority (`mintAuthority`/`mint_authority`/
`mutableMetadata`, `freezeAuthority`/`freeze_authority`/`freezeable`)
and logs the full raw payload if none match - the same "fail
informatively, not silently" pattern that made every other Birdeye
field-name issue tonight fast to diagnose and fix.

**Honest expectation**: given tonight's track record, some adjustment
to these field-name guesses after the first real run should be expected,
not treated as a surprise.

---

## ADR-027: `/defi/token_security` requires a paid tier - and the composite engine handled it correctly without any code change

**Context**: Running the full Phase 10 composite report for the first
time produced a `401` on `/defi/token_security`, using the same API key
that works on every other Birdeye endpoint in this project.

**Finding**: this is the fourth time tonight a Birdeye endpoint's real
access requirements didn't match what was assumed or documented
(after WebSocket access, `multi_price`, and holder response field
casing). The evidence pattern - identical key, working everywhere else,
failing only here - matches the same paid-tier-restriction shape as
`multi_price` earlier tonight. Treating this as confirmed: token
security metadata (mint/freeze authority) is not available on the free
Standard tier.

**What actually happened next is the interesting part**: no code change
was needed. `compute_rug_risk_score()` was built from the start (this
same session, a few hours earlier) to treat a missing signal as a
recorded data gap contributing zero points - not a crash, not an
assumed worst case. The real run proved this design decision correct
under real conditions, not just under test: holder concentration and
dev-wallet outflow both succeeded, security metadata failed, and the
system produced a complete, correctly-scored, honestly-labeled report
anyway (25/100 MODERATE, with both missing signals clearly listed as
data gaps rather than hidden).

**Decision**: no adapter/scoring code changes needed. Documenting this
tier limitation here for the record, and noting a genuine, free
alternative worth pursuing as future work rather than immediately: mint
authority and freeze authority are literally fields in an SPL Token
mint account's on-chain data - queryable directly via any Solana RPC's
`getAccountInfo` (including Helius's own RPC, already integrated and
already free) with no paid Birdeye tier required at all. This would
also arguably be a MORE trustworthy source than a third-party's security
summary, since it reads the actual on-chain account data directly rather
than through an intermediary's interpretation. Not built tonight -
raw SPL account layout parsing is genuinely new, separate work, not a
quick addition at the end of an already very long session - but a clear,
concrete next step rather than a dead end.

**Process reflection, after four of these tonight**: checking a vendor's
actual documented tier-access table before writing code (as done for
WebSocket and `multi_price`) catches some of these; some, like this one
and the holder field-casing issue, only surface on a real run no matter
how carefully the docs are checked beforehand. Both are worth continuing
as practice - check first when possible, and build every integration to
degrade honestly when a real run reveals something docs didn't show.

---

## ADR-028: mint/freeze authority now read directly from on-chain data via Solana RPC

**Context**: ADR-027 found `/defi/token_security` requires a paid
Birdeye tier, leaving mint/freeze authority as a permanent data gap.
This picks up the "genuine free alternative" ADR-027 identified but
didn't build yet.

**Decision**: `rug_pull_intelligence/solana_rpc_adapter.py` calls
Solana's standard JSON-RPC `getAccountInfo` method (with
`encoding: "jsonParsed"`) via Helius's RPC endpoint - already set up in
this project, free tier, no additional signup needed. Mint and freeze
authority are literal fields in an SPL Token mint account's on-chain
data; `jsonParsed` encoding has RPC nodes decode that raw account data
into a structured response using the SPL Token program's own stable,
well-known account layout.

**Why this is a genuine improvement, not just a substitute**: this is
core, standardized Solana RPC behavior, not a single vendor's evolving
REST API surface. Any Solana RPC provider returns the identical shape,
because it's the protocol's own account format being decoded, not one
company's interpretation of it. This is arguably MORE trustworthy than
the original Birdeye-based approach would have been, not merely a
free workaround for a paid feature.

**Verified thoroughly on the parsing side**: 7 tests for
`parse_mint_authority_flags()` covering both authorities active, both
renounced (the safe case), each individually active, explicit null vs.
absent field (both must mean "renounced"), and malformed/unexpected
response shapes returning "unknown" rather than crashing or guessing.
4 tests for the adapter's request construction and retry logic,
including the JSON-RPC-specific case where errors arrive as HTTP 200
with an `error` field in the body, not an HTTP error status - different
from every REST adapter elsewhere in this project, and worth getting
right rather than reusing REST-shaped error handling by copy-paste.

**Wiring change**: `run_security_check.py`'s authority check now uses
`HELIUS_API_KEY` instead of `BIRDEYE_API_KEY` - the old
`BirdeyeSecurityAdapter` code from ADR-026 is left in place (still
correct, still tested) but no longer called from the runner, since it's
confirmed non-functional on the free tier per ADR-027.

**Known limitation carried forward**: this reads the BASE SPL Token
mint layout. Token-2022 mints with extensions (transfer fees, transfer
hooks, etc.) would still correctly report mint/freeze authority (those
fields exist in the same base `info` object regardless of extensions),
but wouldn't surface extension-specific risks like transfer restrictions
or built-in fees - a genuinely separate, more involved piece of parsing
not built here.

---

## ADR-029: News intelligence - deterministic keyword classification, sentiment kept separate from credibility

**Context**: Phase 11 needed to fetch and classify crypto news - event
type (hack, partnership, listing, etc.) and enough context to judge
whether a headline should actually be trusted.

**Provider**: CryptoPanic (free tier, community-vote-based sentiment
built in) rather than building a custom sentiment model - avoids
needing an LLM/ML pipeline for something a purpose-built news aggregator
already does, consistent with this project's "don't build what a
reasonable existing tool already provides" approach.

**Classification approach**: deterministic keyword rules
(`news_intelligence/news_classification.py`), not an LLM or ML model -
same "deterministic rules first" philosophy as the Phase 10 security
engine's mint/freeze authority checks. Every rule is a short, auditable
keyword list a human can read and verify; there's no black box to
debug when a headline gets misclassified.

**Core design decision, directly from the project's original
requirement ("do not equate sentiment with credibility")**: event type
and source credibility are computed as two INDEPENDENT dimensions, not
blended into one score. A sensational headline from an unknown blog and
a terse announcement from a reputable outlet about the same real event
must be distinguishable - collapsing them into one number would hide
exactly that distinction. Verified with a test asserting a hack
headline from an unknown domain correctly reports `event_type=hack` AND
`credibility=UNKNOWN` independently, neither dimension affecting the
other's classification.

**Credibility list is a deliberately small, conservative starting
point** (a handful of well-known outlets), not an authoritative
database - a domain not on the list is `UNKNOWN`, never automatically
treated as low-credibility. Absence of a rating is different from a bad
rating, the same "missing data isn't the worst case" principle used
throughout this project's scoring logic (Phase 9's concentration tiers,
Phase 10's rug-risk score).

**Honest limitation, same pattern as tonight's other integrations under
time pressure**: CryptoPanic's exact API plan slug and response field
names are based on their long-standing, generally documented API shape,
not a live-verified response - built this way given real time
constraints tonight. Some adjustment after the first live run should be
expected, same as several other integrations tonight.

---

## ADR-030: CryptoPanic uses stable `/api/v1/posts/`, not a plan-slug URL

**Context**: Real run of the news adapter failed with `404 Not Found`
on `https://cryptopanic.com/api/free/v2/posts/` - the plan-slug URL
pattern (`/api/{plan}/v2/`) built under time pressure in ADR-029 turned
out to be wrong.

**Fix**: found a real, working community integration (a Glance dashboard
widget actively using CryptoPanic) using
`https://cryptopanic.com/api/v1/posts/` - no plan slug at all, just the
stable v1 API. Confirmed against real, working usage rather than
docs text alone, since the docs page itself renders via JavaScript this
project's tooling can't execute (same limitation noted for the Phase 10
security endpoint).

**Decision**: adapter now uses this confirmed URL. The `plan` constructor
parameter was removed entirely - it modeled a URL structure that isn't
actually how the API works.

**Process note**: this is a good example of the "build under time
pressure, fix quickly once real evidence appears" pattern working as
intended - the fix took one search (finding real working code, not more
guessing) and a small, isolated change, not a redesign.

---

## ADR-031: CryptoPanic free tier discontinued - switched to RSS feeds, no API key needed

**Context**: Real run of the CryptoPanic adapter (fixed to the correct
`/api/v1/posts/` URL in ADR-030) still returned `403 Forbidden`.
Checking CryptoPanic's own API Reference page directly (not docs text
alone, but the actual authenticated reference page) showed a first-
party notice: **"The free Developer API plan is discontinued and will
be removed on April 1st, 2026. Please upgrade to a paid plan to
continue using the API."** The 403 was correct the whole time - this
wasn't a URL or field-name bug at all, the free tier itself no longer
exists.

**Decision**: switched to RSS feeds from established crypto news
outlets (Cointelegraph, CoinDesk), parsed with `feedparser` - a mature,
extremely stable Python library, not a hand-rolled XML parser.

**Why this is structurally safer, not just a workaround**: RSS is a
decades-old, standardized format. Every vendor-specific fix needed
tonight (Birdeye's holder/security field names, CryptoPanic's URL
structure) came from guessing at ONE company's particular, evolving
REST API shape. `feedparser` normalizes RSS/Atom variations into a
consistent set of fields (`entry.title`, `entry.link`, `entry.summary`,
`entry.published_parsed`) regardless of which outlet's raw XML looks
like - the specific uncertainty that caused repeated fixes tonight
doesn't apply the same way here, since the library's own stable
interface is what's being relied on. It also needs no API key, no
signup, and can't be discontinued out from under this project the way
a single vendor's free tier just was.

**Verification note, different from tonight's other adapters**: unlike
most other integrations tonight (built against docs alone, unverified
until a live run), this was tested against REAL feedparser parsing of a
real, valid RSS XML string (not a mock of feedparser's output) - 8
tests, including one confirming a failing feed doesn't block results
from a working one. The one thing NOT verified without live network
access is whether Cointelegraph/CoinDesk's actual current feeds are
reachable and well-formed right now - `feedparser` itself is proven
correct against realistic RSS structure either way.

**CryptoPanic code left in place**: `cryptopanic_adapter.py` and its
tests are still correct, tested, and useful reference for anyone who
does have a paid CryptoPanic plan - just no longer called by the
runner, same pattern as the earlier Birdeye security adapter after
ADR-027.

---

## ADR-032: Phase 12 decision engine - explicit overrides checked first, honest scope limits

**Context**: Phase 12 needed to combine every signal built across
Phases 6-11 into one final, explainable action recommendation, per the
project's original DECISION ENGINE design.

**Scope decision, stated plainly**: this first slice only produces
position-independent actions (BUY, HOLD, AVOID), not the full action
vocabulary (ADD, TAKE_PARTIAL_PROFIT, REDUCE_POSITION, EXIT) from the
original design - those genuinely require knowing whether a position is
already held, which needs Phase 13's paper-trading/position tracker,
not built yet. When a CRITICAL security signal fires, the output
explicitly notes this would be an EMERGENCY_EXIT if a position were
held - surfaced as real information rather than either faking an action
this module can't correctly compute, or silently dropping the insight.

**Core design, directly from the project's stated requirements**:
"Do not treat any security API as an unquestionable source of truth"
and security overrides "must be explicit and auditable." Implemented as
literal PRIORITY ORDER in `decision_logic.py`: override conditions
(CRITICAL/VERY_HIGH rug-risk tier, or an acute-negative news event type
like hack/exploit/rug_allegations) are checked FIRST, before the entry
model's probability is even examined. Verified with a test asserting a
CRITICAL risk tier overrides even a 0.99 (near-maximum) bullish entry
probability - the single most important property of this module, and
the literal point of building overrides as a checked-first priority
rather than one input blended into a weighted score alongside others.

**Entry model deliberately NOT wired to the real Phase 6 model in this
slice**: that model's own metrics (documented back in Phase 6) showed
ROC-AUC of 0.24 - worse than random guessing, on a single week of a
single token's data. Passing `entry_model_probability=None` in the
runner is a deliberate choice to correctly reflect "not used" rather
than silently trusting a signal already known not to be trustworthy.

**Precise, verified consequence of that choice, not just described
approximately**: tracing `decision_logic.py`'s branch structure with
`entry_model_probability` always `None` shows `BUY` is currently
UNREACHABLE through this runner - confirmed by direct testing, not
just read from the code. Only `AVOID` (via an override) or `HOLD`
(everything else) can actually be produced right now. This is stated
precisely here (an initial draft of this doc said BUY would just be
"less likely," which was wrong when actually checked) because an
approximately-true description of a decision engine's real behavior is
exactly the kind of inaccuracy this project's own values argue against.
Wiring in a genuinely better-trained model later, changing one hardcoded
line in `run_decision_check.py`, is what would make BUY reachable.

**A real bug caught before it ever ran**: while wiring this together,
`BirdeyeHolderAdapter` was initially imported from
`rug_pull_intelligence.birdeye_holder_adapter` - it actually lives in
`wallet_intelligence.birdeye_holder_adapter` (built back in Phase 9).
Caught by actually attempting to import the finished module (not just
running the lint/format tools, which flag import-order style issues but
don't verify a module PATH resolves to anything real) before considering
this done - the same "prove it, don't assume it" discipline applied
throughout tonight, this time applied to wiring rather than to a live
API call.

11 tests for the decision logic (covering both override paths, the
non-override BUY/HOLD/AVOID paths, missing-signal handling, and the
explicit-reasons-not-hidden-scoring property). 148/148 tests passing
overall, lint/format clean.

---

## ADR-033: SQLite for paper-trading positions, not the original PostgreSQL plan

**Context**: Phase 13 needed positions to persist BETWEEN separate
script runs - a genuinely new requirement, different from almost
everything built so far tonight (mostly stateless fetch-and-report).
The project's original stack mentions PostgreSQL for exactly this kind
of relational/transactional data.

**Decision**: SQLite - a single local file, zero setup - not a
PostgreSQL server. This project's actual scale (one user, a personal
paper-trading ledger, a modest number of positions) doesn't need a
database server at all. Production alternative, stated plainly per this
project's cost-awareness practice: PostgreSQL, if this project ever
needs multi-user or concurrent access - genuinely unnecessary for what
this is right now.

**Verified with a real database, not mocks**: `test_position_store.py`
uses an actual temporary SQLite file per test (not a mocked connection)
- the round-trip test in particular (open a position, take a partial
profit, save, reload, and assert every field including the nested
`profit_taking_history` list survives correctly) is exactly the kind of
persistence-layer test that's worth running against the real thing,
since JSON serialization of nested data is a common, easy place for a
silent bug to hide.

## ADR-034: partial-sell semantics - relative to remaining size, not original

**Context**: the project's original design says "Support staged profit
taking. Example: sell 20%, hold 80%." - but doesn't specify what a
SECOND "sell 20%" means after the first sale: 20% of the original
position, or 20% of what's left now?

**Decision**: `take_partial_profit()`'s `sell_fraction` is always
relative to the position's CURRENT remaining size, not its original
entry size. Stated explicitly in the module docstring rather than left
implicit, since this is exactly the kind of ambiguity that's easy to
get wrong silently - two reasonable people could read "sell 20%"
differently, and the code needs one specific, documented answer.

**Why this interpretation**: it's the only one that stays well-defined
after multiple partial sells - a fixed original-size percentage could
eventually try to sell more than remains. It also matches how a person
naturally describes staged profit-taking in conversation ("I'll sell
another 20% here" naturally means 20% of what I still have, not 20% of
what I started with).

**Verified explicitly**: a dedicated test (`test_second_partial_sell_
is_relative_to_remaining_not_original`) opens a position, sells 50%,
then sells 50% again, and asserts the remaining size is 25% of the
original (50% of the remaining 50%) - not 0% (which a
naive-original-size interpretation would produce on a second 50% sale).

19 tests for position_math.py (P&L calculation correctness, the
partial-sell semantic, immutability of returned Position objects,
input validation, and full-close-via-100%-sell behavior), 6 for
position_store.py (real SQLite round-trips). 174/174 tests passing
overall, lint/format clean.

---

## ADR-035: champion/challenger promotion - multi-criteria, not single-metric

**Context**: Phase 14 needed to implement the project's explicit MLOps
requirement: "Never automatically replace a better model with a worse
model merely because the newer model was trained more recently."

**Decision**: `model_retraining/promotion_logic.py` requires TWO
conditions before promoting a challenger, not one:
1. ROC-AUC must improve by at least a minimum threshold (0.02) -
   not just any positive difference, since a coin-flip-sized
   improvement isn't reliable evidence of a real improvement.
2. Calibration (Brier score) must not regress beyond a small tolerance
   (0.01), even if ROC-AUC improved.

**Why the second criterion matters, concretely**: a model can improve
ROC-AUC (its ability to RANK predictions correctly) while becoming
worse-calibrated (its predicted probabilities becoming less accurate as
actual probabilities) - for example, by pushing predictions toward more
extreme, overconfident values. Verified with a test constructing exactly
this case (`test_improved_auc_but_regressed_calibration_is_rejected`) -
a challenger with meaningfully better ROC-AUC but meaningfully worse
Brier score is correctly REJECTED, not promoted on the ROC-AUC
improvement alone.

**Missing-data handling, same principle as every scoring module built
tonight**: a missing PRIMARY metric (ROC-AUC) blocks promotion entirely
(can't evaluate without it); a missing SECONDARY metric (Brier score)
skips only that check, still allowing promotion based on ROC-AUC alone
- an unavailable secondary signal shouldn't block a clear primary-signal
improvement, but an unavailable primary signal genuinely can't be
worked around.

## ADR-036: MLflow with SQLite backend - same pattern as Phase 13

**Context**: Phase 14 needed real experiment tracking and model
registry functionality, per the project's original MLOps design
("Use MLflow for: experiments, metrics, parameters, artifacts, model
versions, model registry, model lineage").

**Decision**: `sqlite:///mlflow.db` as the tracking URI - a single
local file, not a separate MLflow tracking server. Same reasoning as
Phase 13's position storage (ADR-033): this project's actual scale (one
user, local experimentation) doesn't need a server, and SQLite backend
still provides MLflow's full experiment tracking and model registry
capability, not a reduced feature set. Production alternative, stated
plainly: run `mlflow server` with a proper backend store if this ever
needs multi-user/remote access.

**A separate, simpler local "champion registry" (JSON file) exists
ALONGSIDE MLflow, not instead of it** - deliberately, not redundantly:
MLflow tracks every run's full history/lineage regardless of promotion
status (the point of an experiment tracker); the champion registry
tracks only "which one is currently the champion" for fast lookup
during a promotion check, without needing to query MLflow's run history
to reconstruct that state each time.

**Verified end-to-end with real MLflow, not mocks**: logged two actual
runs to a real MLflow SQLite-backed tracking store, then ran the actual
`run_promotion_check.run()` function against them (not a simulated
version) - confirmed the "no champion yet, promote automatically" path
AND the "genuinely better challenger, promote with correct reasons"
path both work correctly against MLflow's real `search_runs()` query
mechanism, not just against hand-constructed metric dicts in unit tests.

25 new tests (10 for promotion decision logic including the calibration-
gaming rejection case; 5 for the champion registry's real file I/O).
189/189 tests passing overall, lint/format clean.

---

## ADR-037: MLflow rejects `None` metric values - a real integration gap between two already-correct pieces of code

**Context**: Real run of `ml.run_training` (now with MLflow tracking
from Phase 14) crashed with `MlflowException: Missing value for
required parameter 'metrics[3].value'` while training the entry model.

**Root cause**: two separately-correct pieces of code that had never
been run together before. Phase 6's training code already correctly
returns `roc_auc: None` when a test set has zero positive examples
(ROC-AUC is mathematically undefined in that case - a deliberate,
already-tested "return None, don't crash" design, not a bug). Phase
14's new MLflow logging code, written without accounting for this
pre-existing possibility, passed that `None` straight to
`mlflow.log_metrics()` - which validates every metric value must be an
actual float and rejects `None` outright. Neither piece of code was
individually wrong; the integration between them was.

**Fix**: `_log_metrics_safely()` filters out `None`-valued metrics
before calling MLflow, logging a clear warning explaining exactly which
metric was skipped and why ("this is expected when a test set has zero
positive examples, not an error") - same "fail informatively, keep the
None-means-undefined signal intact rather than silently dropping it or
crashing" pattern used throughout this project.

**Verified by exactly reproducing the real failure, not just reasoning
about it**: constructed the identical metrics dict from the real crash
(including the literal `roc_auc: None`) and confirmed the fixed code
path logs the other 6 valid metrics successfully with no crash, before
this fix was considered done.

**Process note**: this is a good illustration of why the "run it for
real before considering it finished" discipline matters even when both
halves of an integration have already been individually tested -
`ml.run_training`'s existing test suite never exercised the
zero-positive-examples-in-test-set case at the SAME time as the new
MLflow logging code, because that specific combination only actually
occurred with this project's real, current data distribution.

---

## ADR-038: ROC-AUC unavailable for promotion comparison - known data-volume limitation, not a bug

**Context**: Phase 14's promotion logic requires ROC-AUC to compare
champion vs. challenger models. All 4 real MLflow runs so far show
`positive_rate_test = 0.0` - meaning the chronological test split
(most recent ~585 rows) contains zero examples of the "+10% before
-5%" event being predicted, so ROC-AUC is mathematically undefined
and correctly omitted from MLflow logging (see `_log_metrics_safely`
in ml/run_training.py).

**Root cause, confirmed empirically**: total training data is small
(2,336 rows) and the positive event is rare (~0.51% positive rate in
training, ~12 total positive examples). The chronological split
(`ml/entry/split.py`) is correct and deliberate - random splitting
would leak future information into training - so this is a genuine
consequence of limited data volume, not a split-logic bug.

**Decision**: no code change. This resolves naturally as Phase 7's
Airflow pipeline accumulates more historical data over time, giving
the rare positive event more chances to appear in both train and
test windows. Revisit once training data volume grows meaningfully
(e.g. reassess after 30+ more days of ingestion).

**Known limitation stated plainly**: until then, promotion checks for
the entry model will correctly REJECT any challenger, since ROC-AUC
comparison has no data to work with. This is safe (no bad promotion
can occur) but means no promotion will happen until more data exists.
