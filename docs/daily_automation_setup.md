# Daily Automation via launchd

Runs the full pipeline (ingest -> Silver -> Gold -> backtest -> training)
automatically once a day, using macOS's native `launchd` scheduler - not
`cron`, which modern macOS sandboxes and Apple has effectively deprecated
in favor of `launchd`.

This is independent of Airflow (Phase 7) - `launchd` runs the same
underlying scripts directly, sidestepping the scheduler-dispatch
limitation documented in ADR-017 entirely, since it's a completely
different scheduling mechanism with no shared code path.

## One-time setup

**1. Confirm the paths in the script match your machine.**

Open `scripts/run_daily_pipeline.sh` and check the three lines under
"Configuration" - `PROJECT_ROOT` should already match
(`/Users/150ril/Documents/CRYPTO-INTELLIGENCE-`), but double check if
you ever move the project folder.

**2. Test the script manually first, before scheduling it.**

`launchd` jobs run with a minimal environment and no visible output, so
if something's going to fail, you want to find that out interactively
first, not silently at 7 AM:

```
cd ~/Documents/CRYPTO-INTELLIGENCE-
./scripts/run_daily_pipeline.sh
```

Watch it run through all 5 stages. If it completes with "Daily pipeline
run finished successfully", you're ready to schedule it.

**3. Copy the plist file to launchd's config directory:**

```
cp scripts/com.cryptointelligence.dailypipeline.plist ~/Library/LaunchAgents/
```

**4. Load it:**

```
launchctl load ~/Library/LaunchAgents/com.cryptointelligence.dailypipeline.plist
```

That's it - it's now scheduled to run every day at 7:00 AM. Nothing
happens immediately (`RunAtLoad` is set to `false`); it waits for the
next scheduled time.

## Changing the schedule time

Edit the `Hour`/`Minute` values in the plist file, then reload it:

```
launchctl unload ~/Library/LaunchAgents/com.cryptointelligence.dailypipeline.plist
cp scripts/com.cryptointelligence.dailypipeline.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.cryptointelligence.dailypipeline.plist
```

## Checking it actually ran

Two places to look:

```
ls -la logs/daily/                    # one timestamped log file per run
cat logs/daily/<most-recent-file>.log
```

If a run never even started (a `launchd`-level problem, not a script
problem), check:

```
cat logs/launchd_stdout.log
cat logs/launchd_stderr.log
```

## Stopping it

```
launchctl unload ~/Library/LaunchAgents/com.cryptointelligence.dailypipeline.plist
```

This stops future scheduled runs. The plist file itself remains in
`~/Library/LaunchAgents/` until you delete it or `load` it again.

## Running it manually anytime, on demand

You don't need to wait for the schedule - just run the script directly
whenever you want fresh data:

```
./scripts/run_daily_pipeline.sh
```
