"""
Phase 15 dashboard - first slice.

Renders real data from data_access.py. Contains NO business logic of
its own - if a number looks wrong here, the bug is in data_access.py
or an earlier phase, not in this file. This file's only job is
layout/presentation.
"""

import streamlit as st

from dashboard.data_access import (
    get_open_positions,
    get_recommendation_for_token,
    get_watchlist,
)

st.set_page_config(page_title="Crypto Intelligence", layout="wide")

st.title("Crypto Intelligence & Risk Platform")
st.caption(
    "Research and paper-trading tool. Recommendations are probabilistic, "
    "not certain - this system cannot guarantee profitable trades."
)

st.header("Open Positions")

positions = get_open_positions()

if not positions:
    st.info("No open positions.")
else:
    for pos in positions:
        with st.container(border=True):
            col1, col2, col3 = st.columns(3)

            col1.metric("Token", pos.token_address[:8] + "...")
            col1.write(f"Entry: ${pos.entry_price:.4f}")

            if pos.current_price is not None:
                col2.metric(
                    "Current Price",
                    f"${pos.current_price:.4f}",
                    delta=f"{pos.unrealized_pnl_pct:.2%}",
                )
            else:
                col2.warning("Price unavailable")

            if pos.unrealized_pnl_usd is not None:
                col3.metric("Unrealized P&L", f"${pos.unrealized_pnl_usd:.2f}")
            else:
                col3.write("P&L unavailable")

            st.divider()
            st.subheader("Recommendation")

            recommendation = get_recommendation_for_token(pos.token_address)

            if recommendation is None:
                st.warning("Unable to compute recommendation right now.")
            else:
                action_colors = {
                    "EMERGENCY_EXIT": "🔴",
                    "AVOID": "🔴",
                    "EXIT": "🟠",
                    "REDUCE": "🟠",
                    "TAKE_PARTIAL_PROFIT": "🟡",
                    "HOLD": "🔵",
                    "ADD": "🟢",
                    "BUY": "🟢",
                }
                icon = action_colors.get(recommendation.action, "⚪")

                st.markdown(f"### {icon} {recommendation.action}")
                st.progress(recommendation.confidence)
                st.caption(f"Confidence: {recommendation.confidence:.0%}")

                if recommendation.reasons:
                    st.markdown("**Why:**")
                    for reason in recommendation.reasons:
                        st.markdown(f"- {reason}")

                if recommendation.risks:
                    st.markdown("**Risks:**")
                    for risk in recommendation.risks:
                        st.markdown(f"- ⚠️ {risk}")

                if recommendation.would_emergency_exit_if_held:
                    st.error(
                        "This would be classified as EMERGENCY_EXIT if "
                        "conditions were currently held against this position."
                    )


st.header("Watchlist")
st.caption("Tokens you're tracking, no open position yet.")

watchlist_items = get_watchlist()

if not watchlist_items:
    st.info("Watchlist is empty.")
else:
    for item in watchlist_items:
        with st.container(border=True):
            wcol1, wcol2 = st.columns(2)
            wcol1.write(f"Token: {item.token_address[:8]}...")

            if item.current_price is not None:
                wcol1.metric("Current Price", f"${item.current_price:.4f}")
            else:
                wcol1.warning("Price unavailable")

            if item.recommendation is None:
                wcol2.warning("Unable to compute recommendation.")
            else:
                wcol2.markdown(f"**{item.recommendation.action}**")
                wcol2.progress(item.recommendation.confidence)
                wcol2.caption(f"Confidence: {item.recommendation.confidence:.0%}")

st.divider()
st.caption(
    "This is a personal research/paper-trading tool. Nothing here is "
    "financial advice, and no recommendation is guaranteed to be correct."
)
