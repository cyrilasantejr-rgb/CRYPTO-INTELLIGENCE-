# Phase 8: Near-Real-Time Streaming - Setup

Polls Birdeye every ~20 seconds for your watchlist, publishes updates to
Redpanda (Kafka-compatible), and a consumer watches for meaningful price
moves and logs alerts.

**Not true real-time** - see ADR-019 for why (Birdeye's free tier has no
WebSocket access; genuine push-based real-time costs $199/mo minimum).
This is fast polling, ~20 second latency, at $0 cost.

## One-time setup

Start Redpanda alongside MinIO (both defined in the same
`docker-compose.yml` now):

```
docker compose up -d
```

Confirm both are running:

```
docker ps
```

You should see `minio` and `redpanda` containers, both healthy.

## Running it

Two long-running processes, each in its own terminal tab - unlike every
earlier phase's scripts, these don't exit on their own, they run until
you press Ctrl+C:

**Tab 1 - the producer** (polls Birdeye, publishes to Kafka):

```
source venv/bin/activate
python3 -m streaming.producers.market_price_producer
```

**Tab 2 - the consumer** (reads from Kafka, detects and logs alerts):

```
source venv/bin/activate
python3 -m streaming.consumers.market_price_consumer
```

Leave both running. Every ~20 seconds, the producer polls and publishes;
the consumer logs a plain price update for small moves, or a `WARNING`level
alert (WATCH/WARNING/HIGH/CRITICAL) when a token moves enough within
the rolling 5-minute window.

## Adding more tokens to the watchlist

Edit the `WATCHLIST` list in
`streaming/producers/market_price_producer.py`. It's hardcoded there
deliberately for now (see the code comment) rather than loaded from
config - a real multi-token watchlist system is a reasonable future
enhancement, not built yet.

## Known limitation

Alert history is in-memory only (see ADR-020) - if you restart the
consumer, its rolling-window memory of recent prices resets to empty for
every token. This means right after a restart, no alerts will fire until
at least two price observations have come in for a given token.
