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
