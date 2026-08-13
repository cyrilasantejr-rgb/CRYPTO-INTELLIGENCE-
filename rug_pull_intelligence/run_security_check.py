"""
Phase 10: orchestrates every signal built so far (holder concentration,
dev-wallet outflow, token security metadata) into one final, explainable
RUG_RISK_SCORE report for a token.

This is deliberately thin - all the actual logic (fetching each signal,
computing the composite score) lives in already-tested modules from
Phase 9 and this phase. This script's only job is wiring them together
and presenting the result, matching the project's established pattern
of keeping orchestration separate from logic.

Usage:

    python3 -m rug_pull_intelligence.run_security_check \
        --token <token_mint_address> [--dev-wallet <wallet_address>]

--dev-wallet is optional: without it, dev-wallet outflow monitoring is
skipped (recorded as a data gap, not silently ignored) - not every
token has a known/public dev wallet address handy.
"""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from rug_pull_intelligence.mint_authority import parse_mint_authority_flags
from rug_pull_intelligence.security_scoring import compute_rug_risk_score
from rug_pull_intelligence.solana_rpc_adapter import SolanaMintInfoAdapter
from wallet_intelligence.birdeye_holder_adapter import BirdeyeHolderAdapter
from wallet_intelligence.dev_wallet_monitor import analyze_outflows
from wallet_intelligence.helius_wallet_adapter import HeliusWalletAdapter
from wallet_intelligence.holder_concentration import classify_risk_tier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_holder_concentration_tier(birdeye_key: str, token_address: str) -> str | None:
    try:
        adapter = BirdeyeHolderAdapter(api_key=birdeye_key)
        envelope = adapter.fetch_top_holders(token_address, limit=100)
        top10_pct = envelope.payload.get("top10_hold_percent")
        if top10_pct is None:
            logger.warning("Holder data fetched but top10_hold_percent missing")
            return None
        return classify_risk_tier(top10_pct / 100)
    except Exception:
        logger.exception("Failed to fetch holder concentration data")
        return None


def _get_dev_outflow_flag(
    helius_key: str, dev_wallet: str | None, token_address: str
) -> bool | None:
    if dev_wallet is None:
        return None
    try:
        adapter = HeliusWalletAdapter(api_key=helius_key)
        envelope = adapter.fetch_transactions(dev_wallet, limit=100)
        transactions = envelope.payload.get("transactions", [])
        summary = analyze_outflows(transactions, dev_wallet, token_address)
        return summary.has_recent_outflow
    except Exception:
        logger.exception("Failed to fetch dev-wallet outflow data")
        return None


def _get_authority_flags(
    helius_key: str, token_address: str
) -> tuple[bool | None, bool | None]:
    """
    Returns (mint_authority_active, freeze_authority_active), read
    directly from the token's on-chain mint account via Solana RPC - see
    ADR-027 for why this replaced the original Birdeye-based approach
    (that endpoint requires a paid tier this project's account doesn't
    have) and mint_authority.py's docstring for why on-chain RPC is
    actually the more trustworthy source anyway, not just a workaround.
    """
    try:
        adapter = SolanaMintInfoAdapter(helius_api_key=helius_key)
        envelope = adapter.fetch_mint_info(token_address)
        mint_active, freeze_active = parse_mint_authority_flags(envelope.payload)

        if mint_active is None and freeze_active is None:
            logger.warning(
                "Could not parse mint/freeze authority from the RPC response "
                "for %s - raw payload: %s",
                token_address,
                envelope.payload,
            )

        return mint_active, freeze_active
    except Exception:
        logger.exception("Failed to fetch on-chain mint authority data")
        return None, None


def run(token_address: str, dev_wallet: str | None) -> None:
    load_dotenv()
    birdeye_key = os.environ.get("BIRDEYE_API_KEY")
    helius_key = os.environ.get("HELIUS_API_KEY")
    if not birdeye_key:
        raise RuntimeError("BIRDEYE_API_KEY not set.")

    concentration_tier = _get_holder_concentration_tier(birdeye_key, token_address)

    dev_outflow = None
    if dev_wallet is not None:
        if not helius_key:
            logger.warning(
                "--dev-wallet provided but HELIUS_API_KEY not set - skipping"
            )
        else:
            dev_outflow = _get_dev_outflow_flag(helius_key, dev_wallet, token_address)

    mint_active, freeze_active = (None, None)
    if helius_key:
        mint_active, freeze_active = _get_authority_flags(helius_key, token_address)
    else:
        logger.warning(
            "HELIUS_API_KEY not set - mint/freeze authority check skipped "
            "(this now uses on-chain RPC, not Birdeye - see ADR-027)"
        )

    assessment = compute_rug_risk_score(
        holder_concentration_tier=concentration_tier,
        has_recent_dev_outflow=dev_outflow,
        mint_authority_active=mint_active,
        freeze_authority_active=freeze_active,
    )

    logger.info("=== Rug-risk assessment: %s ===", token_address)
    logger.info("RUG_RISK_SCORE: %d / 100", assessment.rug_risk_score)
    logger.info("Risk tier: %s", assessment.risk_tier)
    logger.info("Reasons:")
    for reason in assessment.reasons:
        logger.info("  - %s", reason)
    if assessment.data_gaps:
        logger.warning("Data gaps (signals not available this run):")
        for gap in assessment.data_gaps:
            logger.warning("  - %s", gap)


def main() -> None:
    parser = argparse.ArgumentParser(description="Composite rug-risk security check")
    parser.add_argument("--token", required=True, help="Token mint address")
    parser.add_argument(
        "--dev-wallet",
        required=False,
        default=None,
        help="Dev/creator wallet address to check for outflows (optional)",
    )
    args = parser.parse_args()
    run(args.token, args.dev_wallet)


if __name__ == "__main__":
    main()
