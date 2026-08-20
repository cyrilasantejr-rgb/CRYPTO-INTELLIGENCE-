from discovery.momentum_signal import classify_momentum


def test_strong_when_all_four_metrics_clear_the_strong_bar():
    tier = classify_momentum(
        volume_24h_usd=60_000,
        liquidity=25_000,
        holder=150,
        trade_24h_count=250,
    )
    assert tier == "STRONG"


def test_moderate_when_metrics_clear_moderate_but_not_strong():
    tier = classify_momentum(
        volume_24h_usd=15_000,
        liquidity=8_000,
        holder=60,
        trade_24h_count=60,
    )
    assert tier == "MODERATE"


def test_none_when_below_moderate_thresholds():
    tier = classify_momentum(
        volume_24h_usd=1_000,
        liquidity=500,
        holder=10,
        trade_24h_count=5,
    )
    assert tier is None


def test_one_weak_metric_prevents_strong_even_if_others_are_huge():
    """All four metrics must clear together - huge volume with almost
    no holders is a different, more suspicious shape than genuine
    broad-based activity, even post-quarantine."""
    tier = classify_momentum(
        volume_24h_usd=5_000_000,
        liquidity=500_000,
        holder=5,
        trade_24h_count=1_000,
    )
    assert tier is None


def test_missing_any_metric_returns_none_not_worst_case_but_not_best_case():
    tier = classify_momentum(
        volume_24h_usd=100_000,
        liquidity=None,
        holder=200,
        trade_24h_count=300,
    )
    assert tier is None
