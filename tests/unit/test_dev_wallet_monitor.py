from datetime import datetime, timedelta, timezone

from wallet_intelligence.dev_wallet_monitor import analyze_outflows

WALLET = "DevWalletAddress111"
TOKEN = "TokenMintAddress111"
NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def make_tx(timestamp: datetime, transfers: list[dict]) -> dict:
    return {
        "timestamp": int(timestamp.timestamp()),
        "tokenTransfers": transfers,
    }


def outflow_transfer(
    amount: float, from_wallet: str = WALLET, mint: str = TOKEN
) -> dict:
    return {
        "mint": mint,
        "fromUserAccount": from_wallet,
        "toUserAccount": "SomeoneElse",
        "tokenAmount": amount,
    }


def test_no_transactions_returns_zero_not_crash():
    summary = analyze_outflows([], WALLET, TOKEN, now=NOW)
    assert summary.outflow_transaction_count == 0
    assert summary.total_outflow_amount == 0.0
    assert summary.has_recent_outflow is False


def test_single_outflow_detected():
    transactions = [make_tx(NOW - timedelta(hours=1), [outflow_transfer(500.0)])]
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)

    assert summary.outflow_transaction_count == 1
    assert summary.total_outflow_amount == 500.0
    assert summary.has_recent_outflow is True


def test_incoming_transfers_are_not_counted_as_outflow():
    """A transfer INTO the wallet (fromUserAccount is someone else) must
    not be counted - this function is specifically about money LEAVING
    the monitored wallet."""
    incoming = {
        "mint": TOKEN,
        "fromUserAccount": "SomeoneElse",
        "toUserAccount": WALLET,
        "tokenAmount": 1000.0,
    }
    transactions = [make_tx(NOW - timedelta(hours=1), [incoming])]
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)

    assert summary.outflow_transaction_count == 0
    assert summary.total_outflow_amount == 0.0


def test_transfers_of_unrelated_tokens_are_ignored():
    """A dev wallet's history will contain unrelated tokens (SOL, other
    memecoins) - only outflows of the SPECIFIC monitored token count."""
    unrelated = outflow_transfer(9999.0, mint="SomeOtherToken")
    transactions = [make_tx(NOW - timedelta(hours=1), [unrelated])]
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)

    assert summary.outflow_transaction_count == 0


def test_multiple_outflows_are_summed():
    transactions = [
        make_tx(NOW - timedelta(hours=5), [outflow_transfer(100.0)]),
        make_tx(NOW - timedelta(hours=3), [outflow_transfer(250.0)]),
        make_tx(NOW - timedelta(hours=1), [outflow_transfer(50.0)]),
    ]
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)

    assert summary.outflow_transaction_count == 3
    assert summary.total_outflow_amount == 400.0


def test_outflow_outside_recency_window_still_counted_but_not_recent():
    """An old outflow (say, 10 days ago) is still real history and should
    be counted in the totals, but has_recent_outflow should correctly
    say False if it's outside the recency window - these are two
    different questions ('did this ever happen' vs 'is it happening now')."""
    old_outflow = make_tx(NOW - timedelta(days=10), [outflow_transfer(1000.0)])
    summary = analyze_outflows(
        [old_outflow], WALLET, TOKEN, recency_window_hours=24.0, now=NOW
    )

    assert summary.outflow_transaction_count == 1
    assert summary.total_outflow_amount == 1000.0
    assert summary.has_recent_outflow is False


def test_most_recent_outflow_timestamp_is_the_latest_one():
    transactions = [
        make_tx(NOW - timedelta(hours=5), [outflow_transfer(100.0)]),
        make_tx(NOW - timedelta(hours=1), [outflow_transfer(50.0)]),
        make_tx(NOW - timedelta(hours=3), [outflow_transfer(75.0)]),
    ]
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)

    assert summary.most_recent_outflow == NOW - timedelta(hours=1)


def test_transaction_with_no_token_transfers_is_skipped_gracefully():
    """Not every transaction involves token transfers (e.g. a plain SOL
    transfer, or a program interaction with no transfers) - must not
    crash on a missing/empty tokenTransfers list."""
    transactions = [{"timestamp": int(NOW.timestamp())}]  # no tokenTransfers key at all
    summary = analyze_outflows(transactions, WALLET, TOKEN, now=NOW)
    assert summary.outflow_transaction_count == 0


def test_missing_timestamp_does_not_crash_but_excludes_from_recency():
    transfer = outflow_transfer(100.0)
    tx_no_timestamp = {"tokenTransfers": [transfer]}  # no "timestamp" key
    summary = analyze_outflows([tx_no_timestamp], WALLET, TOKEN, now=NOW)

    assert summary.outflow_transaction_count == 1  # still counted in totals
    assert summary.most_recent_outflow is None  # but no valid timestamp to report
    assert summary.has_recent_outflow is False
