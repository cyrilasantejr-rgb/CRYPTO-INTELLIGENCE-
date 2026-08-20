"""
Phase 15: orchestrates discovery (screener) and the decision engine
(Phase 12) into a single scheduled run - the automated "find new coins
and tell me what to do" loop the project's daily automation never
actually wired up. Every piece it calls already exists and is already
tested (discovery/, decision_engine/); this script's only job is
chaining them in sequence and turning the combined result into a
ranked, severity-tagged report, matching this project's established
pattern of keeping orchestration separate from logic.

Usage:

    python3 -m watchlist.run_watchlist_scan
    python3 -m watchlist.run_watchlist_scan --max-to-score 5 --min-liquidity 20000
    python3 -m watchlist.run_watchlist_scan --no-discover     # reuse latest Bronze, skip a fresh Birdeye call
    python3 -m watchlist.run_watchlist_scan --no-write-gold   # dry run, no S3/MinIO writes

COST NOTE: unlike a single manual `run_decision_check --token X` call,
this fans out across N tokens, and decision_engine.compute_recommendation()
makes several external API calls per token (Birdeye holders, Helius
wallet/RPC, an RSS news fetch). --max-to-score bounds this explicitly -
defaults to 10, not "however many discovery found" - to keep API usage
and run time predictable for an unattended, scheduled run.

KNOWN LIMITATION, stated plainly: dev-wallet monitoring is passed as
None for every candidate here, because freshly discovered tokens have
no known dev-wallet mapping yet (that requires the token's creator
address, which discovery's screener response doesn't include - see
ADR entries under wallet_intelligence in docs/decisions.md). This means
the dev-outflow security signal is silently unavailable for every
NEW-coin scan, same as it already is in a manual run_decision_check
call without --dev-wallet. Not hidden, just inherited from an
upstream gap this phase doesn't attempt to fix.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from common.schemas.discovery_filters import DiscoveryFilters
from common.storage.bronze_writer import write_bronze_batch
from common.storage.object_store import ObjectStoreClient
from decision_engine.decision_logic import Recommendation
from decision_engine.run_decision_check import compute_recommendation
from discovery.birdeye_discovery_adapter import BirdeyeDiscoveryAdapter
from discovery.bronze_reader import read_latest_valid_candidates
from discovery.candidate_quality import validate_candidates
from watchlist.scan_logic import WatchlistEntry, rank_watchlist, to_gold_record

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _build_store() -> ObjectStoreClient:
    """Identical construction to discovery/run_candidate_fetch.py - kept
    duplicated rather than extracted to a shared helper, since these are
    currently the only two call sites and a premature shared helper for
    two callers isn't worth the indirection yet (see ADR-002 on avoiding
    speculative abstraction)."""
    return ObjectStoreClient(
        bucket=os.environ.get("S3_BUCKET_NAME", "crypto-intelligence"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_SECRET_KEY"),
    )


def _run_discovery(
    api_key: str, args: argparse.Namespace, store: ObjectStoreClient
) -> None:
    """Runs a fresh Birdeye screener call and writes results to Bronze -
    the same call discovery/run_candidate_fetch.py makes by hand.
    Failures here are logged, NOT fatal: if discovery fails (rate limit,
    API outage), the scan still proceeds against whatever Bronze already
    has from a previous run rather than producing nothing at all - a
    scheduled job should degrade gracefully, not go silent for a day
    because one upstream call had a bad moment."""
    filters = DiscoveryFilters(
        sort_by="volume_24h_usd",
        sort_type="desc",
        min_liquidity=args.min_liquidity,
        max_liquidity=args.max_liquidity,
        min_volume_24h_usd=args.min_volume_24h_usd,
        min_holder=50,
        min_trade_24h_count=50,
        limit=args.discover_limit,
        offset=0,
        chain="solana",
    )
    logger.info("Running discovery screener: %s", filters)
    adapter = BirdeyeDiscoveryAdapter(api_key=api_key, chain="solana")
    try:
        candidates = list(adapter.discover_candidates(filters))
    except (PermissionError, RuntimeError):
        logger.exception(
            "Discovery screener call failed - falling back to latest existing "
            "Bronze data for this scan"
        )
        return

    if not candidates:
        logger.warning("Discovery returned 0 raw candidates this run")
        return

    store.ensure_bucket()
    write_bronze_batch(candidates, store)
    valid, quarantined = validate_candidates(candidates)
    logger.info(
        "Discovery wrote %d candidate(s) to Bronze (%d valid, %d quarantined)",
        len(candidates),
        len(valid),
        len(quarantined),
    )


def _score_candidates(
    candidates: list, max_to_score: int
) -> list[tuple[str, Recommendation]]:
    """Fans compute_recommendation() out across candidates, isolating
    per-token failures so one token's API error can't abort the whole
    scan - the same 'missing data contributes nothing, never crashes
    the batch' principle this codebase already applies inside individual
    signals, applied here at the orchestration level across tokens."""
    results: list[tuple[str, Recommendation]] = []
    for envelope in candidates[:max_to_score]:
        token_address = envelope.token_address
        symbol = envelope.payload.get("symbol")
        try:
            rec = compute_recommendation(
                token_address=token_address,
                dev_wallet=None,
                news_topic=symbol,
            )
            results.append((token_address, rec))
        except Exception:
            logger.exception("Skipping %s: decision check failed", token_address)
    return results


def _print_report(entries: list[WatchlistEntry]) -> None:
    print("\n=== Watchlist Scan Report ===")
    if not entries:
        print("No candidates scored this run.")
        return
    for entry in entries:
        rec = entry.recommendation
        print(
            f"\n[{entry.severity}] {entry.token_address}  "
            f"action={rec.action}  confidence={rec.confidence:.2f}"
        )
        for reason in rec.reasons:
            print(f"    + {reason}")
        for risk in rec.risks:
            print(f"    ! {risk}")


def run(args: argparse.Namespace) -> list[WatchlistEntry]:
    """Returns the ranked entries so the dashboard (or a test) can call
    this without re-parsing stdout - same reasoning as
    decision_engine.compute_recommendation() being split from its own
    CLI's logging wrapper."""
    load_dotenv()
    birdeye_key = os.environ.get("BIRDEYE_API_KEY")
    if not birdeye_key:
        raise RuntimeError("BIRDEYE_API_KEY not set.")

    store = _build_store()

    if not args.no_discover:
        _run_discovery(birdeye_key, args, store)
    else:
        logger.info(
            "--no-discover set: skipping fresh screener call, reading latest "
            "existing Bronze data"
        )

    candidates = read_latest_valid_candidates(store)
    if not candidates:
        logger.warning("No valid discovery candidates available - nothing to score")
        _print_report([])
        return []

    results = _score_candidates(candidates, args.max_to_score)
    entries = rank_watchlist(results)
    _print_report(entries)

    if not args.no_write_gold and entries:
        scan_time = datetime.now(timezone.utc)
        records = [to_gold_record(e, scan_time) for e in entries]
        dt = scan_time.strftime("%Y-%m-%d")
        run_id = scan_time.strftime("%H%M%S")
        key = f"gold/watchlist_scans/dt={dt}/{run_id}.parquet"
        store.write_parquet(records, key)
        logger.info("Wrote %d ranked watchlist record(s) to %s", len(records), key)

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discovery -> decision-engine watchlist scan"
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=20,
        help="How many raw candidates to fetch from Birdeye",
    )
    parser.add_argument(
        "--max-to-score",
        type=int,
        default=10,
        help="How many top candidates to run through the decision engine "
        "(bounds API cost/runtime)",
    )
    parser.add_argument("--min-liquidity", type=float, default=5_000)
    parser.add_argument("--max-liquidity", type=float, default=2_000_000)
    parser.add_argument(
        "--min-volume-24h",
        type=float,
        default=10_000,
        dest="min_volume_24h_usd",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip a fresh Birdeye screener call; score against latest "
        "existing Bronze data instead",
    )
    parser.add_argument(
        "--no-write-gold",
        action="store_true",
        help="Skip writing ranked results to the Gold layer",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
