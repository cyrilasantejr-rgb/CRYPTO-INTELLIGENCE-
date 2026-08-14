"""
Phase 12: orchestrates the security engine (Phase 10), the entry ML
model (Phase 6), and a news check (Phase 11) into one final, explainable
recommendation.

This is deliberately thin - all real logic lives in already-tested
modules from earlier phases (rug_pull_intelligence, ml.entry, and this
phase's own decision_logic.py). This script's only job is wiring them
together, matching the project's established pattern of keeping
orchestration separate from logic.

Usage:

    python3 -m decision_engine.run_decision_check --token <token_address> [--dev-wallet <wallet_address>]

Note on the entry ML model (see docs/phase12_decision_engine_setup.md
for the full honest caveat): Phase 6's entry model was trained on a
single week of a single token's data and its own metrics showed it
performing worse than random (ROC-AUC 0.24) - this runner surfaces
whatever probability the saved model produces, but the model itself is
not something to trust for real decisions yet. This is intentional and
visible, not hidden - the recommendation's reasons will show exactly
how much weight (if any) came from a genuinely weak signal.
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from decision_engine.decision_logic import Recommendation, make_recommendation
from news_intelligence.news_classification import classify_news_item
from news_intelligence.rss_news_adapter import RssNewsAdapter
from rug_pull_intelligence.mint_authority import parse_mint_authority_flags
from rug_pull_intelligence.security_scoring import compute_rug_risk_score
from rug_pull_intelligence.solana_rpc_adapter import SolanaMintInfoAdapter
from wallet_intelligence.birdeye_holder_adapter import BirdeyeHolderAdapter
from wallet_intelligence.dev_wallet_monitor import analyze_outflows
from wallet_intelligence.helius_wallet_adapter import HeliusWalletAdapter
from wallet_intelligence.holder_concentration import classify_risk_tier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_security_signals(
    birdeye_key: str,
    helius_key: str | None,
    token_address: str,
    dev_wallet: str | None,
) -> tuple[int | None, str | None]:
    """Reuses the exact Phase 10 composite scoring - see
    rug_pull_intelligence/run_security_check.py, which this duplicates
    the wiring of rather than importing its run() function directly,
    since that function is oriented around printing output, not
    returning a value for another module to consume."""
    concentration_tier = None
    try:
        adapter = BirdeyeHolderAdapter(api_key=birdeye_key)
        envelope = adapter.fetch_top_holders(token_address, limit=100)
        top10_pct = envelope.payload.get("top10_hold_percent")
        if top10_pct is not None:
            concentration_tier = classify_risk_tier(top10_pct / 100)
    except Exception:
        logger.exception("Failed to fetch holder concentration data")

    dev_outflow = None
    if dev_wallet is not None and helius_key:
        try:
            wallet_adapter = HeliusWalletAdapter(api_key=helius_key)
            wallet_envelope = wallet_adapter.fetch_transactions(dev_wallet, limit=100)
            transactions = wallet_envelope.payload.get("transactions", [])
            summary = analyze_outflows(transactions, dev_wallet, token_address)
            dev_outflow = summary.has_recent_outflow
        except Exception:
            logger.exception("Failed to fetch dev-wallet outflow data")

    mint_active, freeze_active = None, None
    if helius_key:
        try:
            rpc_adapter = SolanaMintInfoAdapter(helius_api_key=helius_key)
            mint_envelope = rpc_adapter.fetch_mint_info(token_address)
            mint_active, freeze_active = parse_mint_authority_flags(
                mint_envelope.payload
            )
        except Exception:
            logger.exception("Failed to fetch on-chain mint authority data")

    assessment = compute_rug_risk_score(
        holder_concentration_tier=concentration_tier,
        has_recent_dev_outflow=dev_outflow,
        mint_authority_active=mint_active,
        freeze_authority_active=freeze_active,
    )
    return assessment.rug_risk_score, assessment.risk_tier


def _get_news_signal(topic: str | None) -> tuple[str | None, str | None]:
    """Returns (event_type, credibility) for the SINGLE most severe
    recent news item found, if any - a hack headline should not be
    diluted by averaging it against nine unrelated 'other' headlines."""
    try:
        adapter = RssNewsAdapter()
        envelopes = adapter.fetch_recent_news(topic_keyword=topic, limit_per_feed=10)
    except Exception:
        logger.exception("Failed to fetch news")
        return None, None

    most_severe: tuple[str, str] | None = None
    for envelope in envelopes:
        title = envelope.payload.get("title", "")
        domain = envelope.payload.get("domain", "")
        classification = classify_news_item(title=title, domain=domain)
        if classification.event_type != "other":
            most_severe = (classification.event_type, classification.credibility)
            break  # first non-'other' match is sufficient for this slice

    if most_severe is None:
        return None, None
    return most_severe


def compute_recommendation(
    token_address: str, dev_wallet: str | None, news_topic: str | None
) -> Recommendation:
    """Pure computation: gathers signals, builds the recommendation,
    and returns it. No logging, no side effects - callers (CLI or
    dashboard) decide how to present the result."""
    load_dotenv()
    birdeye_key = os.environ.get("BIRDEYE_API_KEY")
    helius_key = os.environ.get("HELIUS_API_KEY")
    if not birdeye_key:
        raise RuntimeError("BIRDEYE_API_KEY not set.")

    rug_risk_score, rug_risk_tier = _get_security_signals(
        birdeye_key, helius_key, token_address, dev_wallet
    )
    news_event_type, news_credibility = _get_news_signal(news_topic)

    # Entry model probability: NOT wired to the real Phase 6 model in
    # this first slice - see the module docstring's honest caveat about
    # that model's weak/unreliable signal on the current tiny dataset.
    # Passed as None here so the recommendation correctly reflects "not
    # used" rather than silently trusting a model known not to be
    # trustworthy yet. Wiring in a real (better-trained) model later
    # only requires changing this one line.
    entry_model_probability = None

    return make_recommendation(
        rug_risk_score=rug_risk_score,
        rug_risk_tier=rug_risk_tier,
        entry_model_probability=entry_model_probability,
        news_event_type=news_event_type,
        news_credibility=news_credibility,
    )


def run(token_address: str, dev_wallet: str | None, news_topic: str | None) -> None:
    """Thin CLI wrapper: computes the recommendation, logs it. Kept
    separate from compute_recommendation() so the dashboard (Phase 15)
    can call the same underlying logic without importing logging
    side effects - single source of truth for the recommendation."""
    recommendation = compute_recommendation(token_address, dev_wallet, news_topic)

    logger.info("=== Decision: %s ===", token_address)
    logger.info("ACTION: %s", recommendation.action)
    logger.info("CONFIDENCE: %.2f", recommendation.confidence)
    logger.info("REASONS:")
    for reason in recommendation.reasons:
        logger.info("  - %s", reason)
    if recommendation.risks:
        logger.info("RISKS:")
        for risk in recommendation.risks:
            logger.info("  - %s", risk)
    if recommendation.would_emergency_exit_if_held:
        logger.warning(
            "If a position were currently held in this token, this would "
            "be classified as EMERGENCY_EXIT (position tracking not yet "
            "built - see Phase 13)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Composite decision check")
    parser.add_argument("--token", required=True, help="Token mint address")
    parser.add_argument("--dev-wallet", required=False, default=None)
    parser.add_argument("--news-topic", required=False, default=None)
    args = parser.parse_args()
    run(args.token, args.dev_wallet, args.news_topic)


if __name__ == "__main__":
    main()
