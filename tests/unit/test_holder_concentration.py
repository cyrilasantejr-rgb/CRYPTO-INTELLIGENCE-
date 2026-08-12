from wallet_intelligence.holder_concentration import compute_concentration_metrics


def test_empty_holder_list_returns_zero_not_crash():
    metrics = compute_concentration_metrics([])
    assert metrics.holder_count == 0
    assert metrics.top10_concentration_pct == 0.0
    assert metrics.risk_tier == "LOW"


def test_perfectly_even_distribution_is_low_risk():
    """100 holders, each with exactly 1% - top 10 own 10%, well under
    the 20% MODERATE threshold."""
    holders = [1.0] * 100
    metrics = compute_concentration_metrics(holders)

    assert metrics.holder_count == 100
    assert metrics.top10_concentration_pct == 0.10
    assert metrics.risk_tier == "LOW"


def test_single_whale_owning_everything_is_critical():
    holders = [1_000_000.0, 1.0, 1.0, 1.0]
    metrics = compute_concentration_metrics(holders)

    assert metrics.top10_concentration_pct > 0.99
    assert metrics.risk_tier == "CRITICAL"
    # HHI should also be very close to 1.0 - one holder dominates
    assert metrics.hhi > 0.99


def test_hhi_distinguishes_even_top10_from_concentrated_top10():
    """This is the whole point of computing HHI alongside top10%: two
    holder sets with IDENTICAL top10_concentration_pct can have very
    different risk profiles, and HHI is what tells them apart."""
    # Set A: top 10 holders each own exactly 3% (30% total)
    even_top10 = [3.0] * 10 + [0.7] * 100  # 30% + 70%, roughly
    # Set B: one holder owns 21%, other 9 top holders own 1% each (30% total)
    concentrated_top10 = [21.0] + [1.0] * 9 + [0.7] * 100

    even_metrics = compute_concentration_metrics(even_top10)
    concentrated_metrics = compute_concentration_metrics(concentrated_top10)

    # Both have the same top10% (roughly), by construction
    assert abs(even_metrics.top10_concentration_pct - 0.30) < 0.02
    assert abs(concentrated_metrics.top10_concentration_pct - 0.30) < 0.02

    # But HHI reveals concentrated_top10 is riskier - one dominant whale
    assert concentrated_metrics.hhi > even_metrics.hhi


def test_risk_tiers_are_ordered_correctly():
    """Explicitly verify each documented tier boundary, not just spot-check
    one case - these thresholds are meant to mirror the project's
    RUG_RISK_SCORE scale exactly, so getting the boundaries right matters.

    Each case: 10 equal-sized top holders whose combined share hits the
    target top10_concentration_pct exactly, plus a long tail of small
    holders splitting the remainder."""
    # top10 = 15% -> LOW (under 20%)
    assert compute_concentration_metrics([1.5] * 10 + [0.85] * 100).risk_tier == "LOW"
    # top10 = 25% -> MODERATE (20-40%)
    assert (
        compute_concentration_metrics([2.5] * 10 + [0.75] * 100).risk_tier == "MODERATE"
    )
    # top10 = 50% -> HIGH (40-60%)
    assert compute_concentration_metrics([5.0] * 10 + [0.5] * 100).risk_tier == "HIGH"
    # top10 = 70% -> VERY_HIGH (60-80%)
    assert (
        compute_concentration_metrics([7.0] * 10 + [0.3] * 100).risk_tier == "VERY_HIGH"
    )
    # top10 = 90% -> CRITICAL (80%+)
    assert (
        compute_concentration_metrics([9.0] * 10 + [0.1] * 100).risk_tier == "CRITICAL"
    )


def test_fewer_than_ten_holders_still_works():
    """A brand new token might genuinely have fewer than 10 holders total
    - top10_concentration_pct should just sum whatever exists, not crash
    on a short list."""
    metrics = compute_concentration_metrics([50.0, 30.0, 20.0])
    assert metrics.holder_count == 3
    assert metrics.top10_concentration_pct == 1.0  # all 3 holders = 100%


def test_zero_total_supply_does_not_crash():
    """Degenerate case: all reported holder amounts are zero (bad data,
    not a real scenario, but must not raise a ZeroDivisionError)."""
    metrics = compute_concentration_metrics([0.0, 0.0, 0.0])
    assert metrics.top10_concentration_pct == 0.0
    assert metrics.risk_tier == "LOW"
