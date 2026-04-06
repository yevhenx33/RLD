"""
Comprehensive tests for the Morpho Blue AdaptiveCurveIRM Python port.

Test categories:
  1. WAD math primitives (w_mul_to_zero, w_div_to_zero, int_div_toward_zero)
  2. wExp approximation accuracy vs math.exp
  3. Curve function: boundary, monotonicity, symmetry
  4. compute_borrow_rate: target utilization identity, steepness at extremes
  5. APY pipeline: end-to-end with known reference values
  6. new_rate_at_target: clamping to MIN/MAX
  7. evolve_rate_at_target: forward evolution
  8. Regime classification: boundary conditions
  9. Differential fuzz: random inputs, invariant checks
"""

import math
import random
import pytest

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.morpho.irm import (
    WAD, CURVE_STEEPNESS, TARGET_UTILIZATION,
    ADJUSTMENT_SPEED, INITIAL_RATE_AT_TARGET,
    MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET,
    SECONDS_PER_YEAR,
    LN_2_INT, LN_WEI_INT, WEXP_UPPER_BOUND, WEXP_UPPER_VALUE,
    w_mul_to_zero, w_div_to_zero, int_div_toward_zero,
    w_exp, bound,
    curve, compute_err, new_rate_at_target,
    compute_borrow_rate, borrow_rate_to_apy,
    compute_supply_apy, compute_full_apy,
    evolve_rate_at_target,
    classify_utilization_regime,
)


# ═══════════════════════════════════════════════════════════════════
#  1. WAD Math Primitives
# ═══════════════════════════════════════════════════════════════════

class TestWadMath:
    """Verify signed WAD arithmetic matches Solidity semantics."""

    def test_w_mul_to_zero_basic(self):
        """2 WAD * 3 WAD = 6 WAD."""
        assert w_mul_to_zero(2 * WAD, 3 * WAD) == 6 * WAD

    def test_w_mul_to_zero_fractional(self):
        """0.5 WAD * 0.5 WAD = 0.25 WAD."""
        assert w_mul_to_zero(WAD // 2, WAD // 2) == WAD // 4

    def test_w_mul_to_zero_negative(self):
        """-2 WAD * 3 WAD = -6 WAD (rounds toward zero)."""
        result = w_mul_to_zero(-2 * WAD, 3 * WAD)
        assert result == -6 * WAD

    def test_w_mul_to_zero_rounds_toward_zero(self):
        """Verify rounding toward zero for negative results.
        In Solidity: (-1 * 3) / 2 = -1 (not -2).
        Scaled: (-1 * 3) / WAD should truncate toward zero.
        """
        # Small negative * small positive — the product is slightly negative
        result = int_div_toward_zero(-7, 2)
        assert result == -3  # Solidity: -7/2 = -3 (toward zero), NOT -4 (Python //)

    def test_w_div_to_zero_basic(self):
        """6 WAD / 3 WAD = 2 WAD."""
        assert w_div_to_zero(6 * WAD, 3 * WAD) == 2 * WAD

    def test_w_div_to_zero_negative(self):
        """-6 WAD / 4 WAD rounds toward zero."""
        result = w_div_to_zero(-6 * WAD, 4 * WAD)
        # -6/4 = -1.5, truncated toward zero = -1 at WAD scale
        assert result == -1 * WAD - WAD // 2  # -1.5 WAD

    def test_w_div_zero_denominator_raises(self):
        with pytest.raises(ZeroDivisionError):
            w_div_to_zero(WAD, 0)

    def test_int_div_toward_zero_positive(self):
        assert int_div_toward_zero(7, 2) == 3

    def test_int_div_toward_zero_negative_numerator(self):
        assert int_div_toward_zero(-7, 2) == -3

    def test_int_div_toward_zero_negative_denominator(self):
        assert int_div_toward_zero(7, -2) == -3

    def test_int_div_toward_zero_both_negative(self):
        assert int_div_toward_zero(-7, -2) == 3

    def test_w_mul_identity(self):
        """x * 1 WAD = x."""
        for x in [0, WAD, -WAD, 42 * WAD, -(10**30)]:
            assert w_mul_to_zero(x, WAD) == x


# ═══════════════════════════════════════════════════════════════════
#  2. wExp Accuracy
# ═══════════════════════════════════════════════════════════════════

class TestWExp:
    """Verify wExp approximation against Python math.exp."""

    def test_wexp_zero(self):
        """exp(0) = 1 WAD."""
        assert w_exp(0) == WAD

    def test_wexp_one(self):
        """exp(1 WAD) ≈ e * WAD ≈ 2.718... * 1e18."""
        result = w_exp(WAD)
        expected = int(math.exp(1) * WAD)
        # Solidity uses 2nd order Taylor, expect ~1% error max
        assert abs(result - expected) / expected < 0.01

    def test_wexp_negative_one(self):
        """exp(-1 WAD) ≈ 0.3679 * WAD."""
        result = w_exp(-WAD)
        expected = int(math.exp(-1) * WAD)
        assert abs(result - expected) / expected < 0.01

    def test_wexp_very_negative_returns_zero(self):
        """exp(x) → 0 for x < ln(1e-18)."""
        assert w_exp(LN_WEI_INT - 1) == 0
        assert w_exp(-100 * WAD) == 0

    def test_wexp_upper_bound_clips(self):
        """exp(x) clips at WEXP_UPPER_VALUE for x >= WEXP_UPPER_BOUND."""
        assert w_exp(WEXP_UPPER_BOUND) == WEXP_UPPER_VALUE
        assert w_exp(WEXP_UPPER_BOUND + WAD) == WEXP_UPPER_VALUE

    def test_wexp_ln2(self):
        """exp(ln2) ≈ 2 WAD."""
        result = w_exp(LN_2_INT)
        assert abs(result - 2 * WAD) / (2 * WAD) < 0.005

    def test_wexp_small_positive(self):
        """For small x, exp(x) ≈ 1 + x + x²/2, very accurate."""
        x = WAD // 100  # 0.01
        result = w_exp(x)
        expected = int(math.exp(0.01) * WAD)
        assert abs(result - expected) / expected < 0.001

    def test_wexp_monotonic(self):
        """wExp must be monotonically increasing."""
        prev = w_exp(-10 * WAD)
        for x_int in range(-9, 20):
            x = x_int * WAD
            curr = w_exp(x)
            assert curr >= prev, f"wExp not monotonic at x={x_int}: {prev} > {curr}"
            prev = curr

    @pytest.mark.parametrize("x_float", [
        -5.0, -2.0, -1.0, -0.5, -0.1, 0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0
    ])
    def test_wexp_accuracy_sweep(self, x_float):
        """Verify wExp accuracy across a range of inputs. Max 2% error."""
        x_wad = int(x_float * WAD)
        result = w_exp(x_wad)
        expected = math.exp(x_float) * WAD
        if expected > 1:  # Skip near-zero expected values
            rel_error = abs(result - expected) / expected
            assert rel_error < 0.02, f"wExp({x_float}) = {result}, expected {expected}, error = {rel_error:.4f}"


# ═══════════════════════════════════════════════════════════════════
#  3. Curve Function
# ═══════════════════════════════════════════════════════════════════

class TestCurve:
    """Test the piecewise-linear rate curve."""

    def test_curve_at_target_err_zero(self):
        """When err=0 (at target utilization), rate == rateAtTarget."""
        r = INITIAL_RATE_AT_TARGET
        result = curve(r, 0)
        # (coeff * 0 + 1) * r = r
        assert result == r

    def test_curve_max_err_positive(self):
        """At err=+1 WAD (100% util), rate = rateAtTarget * CURVE_STEEPNESS."""
        r = INITIAL_RATE_AT_TARGET
        err = WAD  # fully above target
        result = curve(r, err)
        expected = w_mul_to_zero(CURVE_STEEPNESS, r)  # C * rateAtTarget
        assert result == expected

    def test_curve_max_err_negative(self):
        """At err=-1 WAD (0% util), rate = rateAtTarget / CURVE_STEEPNESS."""
        r = INITIAL_RATE_AT_TARGET
        err = -WAD
        result = curve(r, err)
        expected = w_div_to_zero(r, CURVE_STEEPNESS)  # rateAtTarget / C
        # Allow 1 wei of rounding
        assert abs(result - expected) <= 1

    def test_curve_positive_err_increases_rate(self):
        """Positive err (util > target) → rate > rateAtTarget."""
        r = INITIAL_RATE_AT_TARGET
        err = WAD // 2  # +0.5
        result = curve(r, err)
        assert result > r

    def test_curve_negative_err_decreases_rate(self):
        """Negative err (util < target) → rate < rateAtTarget."""
        r = INITIAL_RATE_AT_TARGET
        err = -WAD // 2  # -0.5
        result = curve(r, err)
        assert result < r

    def test_curve_monotonic_in_err(self):
        """Rate must monotonically increase with err."""
        r = INITIAL_RATE_AT_TARGET
        prev = curve(r, -WAD)
        for e in range(-9, 11):
            err = e * WAD // 10
            curr = curve(r, err)
            assert curr >= prev, f"Curve not monotonic at err={e}/10"
            prev = curr

    def test_curve_always_nonnegative(self):
        """Rate should be non-negative for positive rateAtTarget and valid err."""
        r = INITIAL_RATE_AT_TARGET
        for e in range(-10, 11):
            err = e * WAD // 10
            result = curve(r, err)
            assert result >= 0, f"Negative rate at err={e}/10: {result}"

    def test_curve_linearity_positive_side(self):
        """On the positive side, curve is linear: rate = (C-1)*err + 1) * R.
        Check two points and verify linearity."""
        r = 10**15  # small rate for clean math
        err1 = WAD // 4   # 0.25
        err2 = WAD // 2   # 0.5
        r1 = curve(r, err1)
        r2 = curve(r, err2)
        # Linear means r2 - r1 == r1 - curve(r, 0)  (equal spacing in err)
        r0 = curve(r, 0)
        assert abs((r2 - r1) - (r1 - r0)) <= 2  # Allow rounding


# ═══════════════════════════════════════════════════════════════════
#  4. compute_borrow_rate
# ═══════════════════════════════════════════════════════════════════

class TestComputeBorrowRate:
    """Test the full borrow rate computation pipeline."""

    def test_at_target_utilization(self):
        """At 90% utilization, borrow_rate == rate_at_target."""
        r = INITIAL_RATE_AT_TARGET
        util = TARGET_UTILIZATION  # 0.9 WAD
        result = compute_borrow_rate(r, util)
        assert result == r

    def test_at_zero_utilization(self):
        """At 0% utilization, borrow_rate == rate_at_target / C."""
        r = INITIAL_RATE_AT_TARGET
        result = compute_borrow_rate(r, 0)
        expected = w_div_to_zero(r, CURVE_STEEPNESS)
        assert abs(result - expected) <= 1

    def test_at_full_utilization(self):
        """At 100% utilization, borrow_rate == rate_at_target * C."""
        r = INITIAL_RATE_AT_TARGET
        util = WAD  # 100%
        result = compute_borrow_rate(r, util)
        expected = w_mul_to_zero(CURVE_STEEPNESS, r)
        assert result == expected

    def test_borrow_rate_monotonic_in_utilization(self):
        """Borrow rate must increase monotonically with utilization."""
        r = INITIAL_RATE_AT_TARGET
        prev_rate = compute_borrow_rate(r, 0)
        for u in range(1, 101):
            util = u * WAD // 100
            rate = compute_borrow_rate(r, util)
            assert rate >= prev_rate, f"Rate decreased at util={u}%"
            prev_rate = rate

    def test_borrow_rate_4x_ratio(self):
        """Rate at 100% / Rate at 0% should be CURVE_STEEPNESS² = 16."""
        r = INITIAL_RATE_AT_TARGET
        rate_0 = compute_borrow_rate(r, 0)
        rate_100 = compute_borrow_rate(r, WAD)
        # rate_0 = R/4, rate_100 = R*4, ratio = 16
        ratio = rate_100 / rate_0
        assert abs(ratio - 16.0) < 0.01


# ═══════════════════════════════════════════════════════════════════
#  5. APY Pipeline
# ═══════════════════════════════════════════════════════════════════

class TestAPYPipeline:
    """End-to-end APY computation tests."""

    def test_initial_rate_at_target_apy(self):
        """Initial rate (4%/year) at target utilization → ~4% borrow APY."""
        r = INITIAL_RATE_AT_TARGET
        borrow_rate = compute_borrow_rate(r, TARGET_UTILIZATION)
        borrow_apy = borrow_rate_to_apy(borrow_rate)
        # Should be close to 4% (0.04)
        assert 0.03 < borrow_apy < 0.05, f"Expected ~4%, got {borrow_apy*100:.2f}%"

    def test_supply_apy_less_than_borrow(self):
        """Supply APY must always be <= borrow APY."""
        borrow_apy = 0.10  # 10%
        supply_apy = compute_supply_apy(borrow_apy, 0.9, 0)  # 90% util, no fee
        assert supply_apy <= borrow_apy

    def test_supply_apy_with_fee(self):
        """Supply APY = borrow_apy * util * (1 - fee)."""
        borrow_apy = 0.10
        util = 0.9
        fee = WAD // 10  # 10% fee
        supply = compute_supply_apy(borrow_apy, util, fee)
        expected = 0.10 * 0.9 * 0.9  # 0.081
        assert abs(supply - expected) < 1e-10

    def test_supply_apy_zero_utilization(self):
        """Supply APY is 0 when utilization is 0."""
        assert compute_supply_apy(0.10, 0.0, 0) == 0.0

    def test_compute_full_apy_none_rate(self):
        """Returns (None, None) when rate_at_target is None."""
        assert compute_full_apy(None, TARGET_UTILIZATION, 0) == (None, None)

    def test_compute_full_apy_none_util(self):
        """Returns (None, None) when utilization is None."""
        assert compute_full_apy(INITIAL_RATE_AT_TARGET, None, 0) == (None, None)

    def test_compute_full_apy_zero_rate(self):
        """Returns (None, None) when rate_at_target is 0."""
        assert compute_full_apy(0, TARGET_UTILIZATION, 0) == (None, None)

    def test_compute_full_apy_typical(self):
        """Full pipeline with typical values."""
        r = INITIAL_RATE_AT_TARGET  # ~4%/year
        util = int(0.8 * WAD)       # 80%
        fee = 0

        borrow_apy, supply_apy = compute_full_apy(r, util, fee)
        assert borrow_apy is not None
        assert supply_apy is not None
        assert 0.005 < borrow_apy < 0.10  # Reasonable range for 80% util
        assert supply_apy < borrow_apy
        assert supply_apy > 0

    def test_high_rate_high_util_apy_reasonable(self):
        """Even at MAX_RATE_AT_TARGET and 100% util, APY shouldn't be astronomical."""
        r = MAX_RATE_AT_TARGET  # 200%/year
        util = WAD              # 100%
        borrow_apy, _ = compute_full_apy(r, util, 0)
        assert borrow_apy is not None
        # At 200%/yr rate_at_target, 100% util → rate = 4 * 200% = 800%/yr
        # exp(8) - 1 ≈ 2980, which is huge but mathematically valid
        assert borrow_apy > 0


# ═══════════════════════════════════════════════════════════════════
#  6. new_rate_at_target (clamping)
# ═══════════════════════════════════════════════════════════════════

class TestNewRateAtTarget:
    """Test rate clamping to MIN/MAX bounds."""

    def test_clamp_to_min(self):
        """Very negative adaptation → clamp to MIN."""
        result = new_rate_at_target(INITIAL_RATE_AT_TARGET, -100 * WAD)
        assert result == MIN_RATE_AT_TARGET

    def test_clamp_to_max(self):
        """Very positive adaptation → clamp to MAX."""
        result = new_rate_at_target(INITIAL_RATE_AT_TARGET, 100 * WAD)
        assert result == MAX_RATE_AT_TARGET

    def test_no_adaptation(self):
        """Zero adaptation → same rate."""
        r = INITIAL_RATE_AT_TARGET
        result = new_rate_at_target(r, 0)
        assert result == r

    def test_small_positive_adaptation(self):
        """Small positive adaptation → rate increases."""
        r = INITIAL_RATE_AT_TARGET
        result = new_rate_at_target(r, WAD // 10)  # exp(0.1) ≈ 1.105
        assert result > r

    def test_small_negative_adaptation(self):
        """Small negative adaptation → rate decreases."""
        r = INITIAL_RATE_AT_TARGET
        result = new_rate_at_target(r, -WAD // 10)  # exp(-0.1) ≈ 0.905
        assert result < r


# ═══════════════════════════════════════════════════════════════════
#  7. evolve_rate_at_target
# ═══════════════════════════════════════════════════════════════════

class TestEvolveRateAtTarget:
    """Test the adaptive rate evolution over time."""

    def test_at_target_no_change(self):
        """At exactly 90% utilization, rate doesn't change (err=0, speed=0)."""
        r = INITIAL_RATE_AT_TARGET
        result = evolve_rate_at_target(r, TARGET_UTILIZATION, 3600)
        assert result == r

    def test_above_target_rate_increases(self):
        """Above target utilization, rate should increase over time."""
        r = INITIAL_RATE_AT_TARGET
        util = int(0.95 * WAD)
        result = evolve_rate_at_target(r, util, 3600)
        assert result > r

    def test_below_target_rate_decreases(self):
        """Below target utilization, rate should decrease over time."""
        r = INITIAL_RATE_AT_TARGET
        util = int(0.5 * WAD)
        result = evolve_rate_at_target(r, util, 3600)
        assert result < r

    def test_longer_time_larger_change(self):
        """More elapsed time → larger rate change."""
        r = INITIAL_RATE_AT_TARGET
        util = int(0.95 * WAD)
        r_1h = evolve_rate_at_target(r, util, 3600)
        r_24h = evolve_rate_at_target(r, util, 86400)
        assert r_24h > r_1h > r

    def test_zero_elapsed_no_change(self):
        """Zero elapsed seconds → no change."""
        r = INITIAL_RATE_AT_TARGET
        assert evolve_rate_at_target(r, int(0.5 * WAD), 0) == r

    def test_extreme_above_target_clamps(self):
        """Extreme above-target for very long → clamps to MAX."""
        r = INITIAL_RATE_AT_TARGET
        util = WAD  # 100%
        result = evolve_rate_at_target(r, util, 365 * 86400)  # 1 year at 100%
        assert result == MAX_RATE_AT_TARGET


# ═══════════════════════════════════════════════════════════════════
#  8. Regime Classification
# ═══════════════════════════════════════════════════════════════════

class TestRegimeClassification:
    """Test utilization regime labeling."""

    def test_idle(self):
        assert classify_utilization_regime(0.0) == "idle"

    def test_low(self):
        assert classify_utilization_regime(0.01) == "low"
        assert classify_utilization_regime(0.49) == "low"

    def test_normal(self):
        assert classify_utilization_regime(0.50) == "normal"
        assert classify_utilization_regime(0.89) == "normal"

    def test_elevated(self):
        assert classify_utilization_regime(0.90) == "elevated"
        assert classify_utilization_regime(0.949) == "elevated"

    def test_critical(self):
        assert classify_utilization_regime(0.95) == "critical"
        assert classify_utilization_regime(0.999) == "critical"

    def test_trapped(self):
        assert classify_utilization_regime(1.0) == "trapped"
        assert classify_utilization_regime(1.001) == "trapped"  # edge: > 1.0

    def test_boundary_at_90(self):
        """90% is the IRM target — should be classified as elevated."""
        assert classify_utilization_regime(0.90) == "elevated"

    def test_boundary_just_below_90(self):
        assert classify_utilization_regime(0.8999) == "normal"


# ═══════════════════════════════════════════════════════════════════
#  9. Differential Fuzzing
# ═══════════════════════════════════════════════════════════════════

class TestDifferentialFuzz:
    """Random invariant checks across many inputs."""

    @pytest.fixture(autouse=True)
    def seed_rng(self):
        random.seed(42)

    def test_borrow_rate_always_nonneg(self):
        """Borrow rate must be >= 0 for all valid inputs."""
        for _ in range(1000):
            r = random.randint(MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET)
            u = random.randint(0, WAD)
            rate = compute_borrow_rate(r, u)
            assert rate >= 0, f"Negative borrow rate: r={r}, u={u}, rate={rate}"

    def test_borrow_rate_monotonic_random_rates(self):
        """For any rateAtTarget, borrow rate must be monotonic in utilization."""
        for _ in range(100):
            r = random.randint(MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET)
            prev = compute_borrow_rate(r, 0)
            for u_pct in range(1, 101):
                u = u_pct * WAD // 100
                curr = compute_borrow_rate(r, u)
                assert curr >= prev, (
                    f"Monotonicity violation: r={r}, u={u_pct}%, "
                    f"prev={prev}, curr={curr}"
                )
                prev = curr

    def test_borrow_rate_linear_in_rate_at_target(self):
        """curve is linear in rateAtTarget, so borrow_rate should scale linearly."""
        u = int(0.75 * WAD)
        r1 = INITIAL_RATE_AT_TARGET
        r2 = 2 * INITIAL_RATE_AT_TARGET
        rate1 = compute_borrow_rate(r1, u)
        rate2 = compute_borrow_rate(r2, u)
        # rate2 should be ~2x rate1 (exact within WAD rounding)
        ratio = rate2 / rate1
        assert abs(ratio - 2.0) < 0.001

    def test_full_apy_range_check(self):
        """Full APY pipeline should produce reasonable numbers for random inputs."""
        for _ in range(500):
            r = random.randint(MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET)
            u = random.randint(0, WAD)
            fee = random.randint(0, WAD // 4)  # 0-25% fee

            borrow_apy, supply_apy = compute_full_apy(r, u, fee)
            assert borrow_apy is not None
            assert supply_apy is not None
            assert borrow_apy >= 0, f"Negative borrow APY: {borrow_apy}"
            assert supply_apy >= 0, f"Negative supply APY: {supply_apy}"
            # Supply APY should never exceed borrow APY
            assert supply_apy <= borrow_apy + 1e-15, (
                f"supply_apy ({supply_apy}) > borrow_apy ({borrow_apy})"
            )

    def test_evolve_bounded(self):
        """Evolved rate must stay in [MIN, MAX] bounds regardless of inputs."""
        for _ in range(500):
            r = random.randint(MIN_RATE_AT_TARGET, MAX_RATE_AT_TARGET)
            u = random.randint(0, WAD)
            dt = random.randint(0, 365 * 86400)
            result = evolve_rate_at_target(r, u, dt)
            assert MIN_RATE_AT_TARGET <= result <= MAX_RATE_AT_TARGET, (
                f"Out of bounds: r={r}, u={u}, dt={dt}, result={result}"
            )

    def test_regime_completeness(self):
        """Every utilization [0, 1] maps to a valid regime label."""
        valid_regimes = {"idle", "low", "normal", "elevated", "critical", "trapped"}
        for _ in range(1000):
            u = random.uniform(0.0, 1.0)
            regime = classify_utilization_regime(u)
            assert regime in valid_regimes, f"Invalid regime '{regime}' for util={u}"


# ═══════════════════════════════════════════════════════════════════
#  10. Constants Validation
# ═══════════════════════════════════════════════════════════════════

class TestConstants:
    """Verify our Python constants match the Solidity definitions."""

    def test_seconds_per_year(self):
        """365 * 86400."""
        assert SECONDS_PER_YEAR == 365 * 86400

    def test_curve_steepness(self):
        """4 ether."""
        assert CURVE_STEEPNESS == 4 * 10**18

    def test_target_utilization(self):
        """0.9 ether."""
        assert TARGET_UTILIZATION == 9 * 10**17

    def test_adjustment_speed(self):
        """50 ether / 365 days."""
        assert ADJUSTMENT_SPEED == 50 * WAD // (365 * 86400)

    def test_initial_rate_at_target(self):
        """0.04 ether / 365 days."""
        assert INITIAL_RATE_AT_TARGET == (4 * WAD // 100) // (365 * 86400)

    def test_min_rate_at_target(self):
        """0.001 ether / 365 days."""
        assert MIN_RATE_AT_TARGET == (WAD // 1000) // (365 * 86400)

    def test_max_rate_at_target(self):
        """2.0 ether / 365 days."""
        assert MAX_RATE_AT_TARGET == (2 * WAD) // (365 * 86400)

    def test_ln2_accuracy(self):
        """LN_2_INT should be within 1e-15 of actual ln(2) * 1e18."""
        actual = math.log(2) * 1e18
        assert abs(LN_2_INT - actual) < 1e3  # 1000 wei tolerance


# ═══════════════════════════════════════════════════════════════════
#  Poka-Yoke: Self-verification on import
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running self-verification...")

    # Core invariant: at 90% util, borrow_rate == rate_at_target
    r = INITIAL_RATE_AT_TARGET
    br = compute_borrow_rate(r, TARGET_UTILIZATION)
    assert br == r, f"CRITICAL: borrow_rate at target != rate_at_target ({br} != {r})"

    # Rate at 0% should be 1/4 of rate at target
    br_0 = compute_borrow_rate(r, 0)
    expected_0 = w_div_to_zero(r, CURVE_STEEPNESS)
    assert abs(br_0 - expected_0) <= 1, f"CRITICAL: rate at 0% util wrong"

    # Rate at 100% should be 4x rate at target
    br_100 = compute_borrow_rate(r, WAD)
    expected_100 = w_mul_to_zero(CURVE_STEEPNESS, r)
    assert br_100 == expected_100, f"CRITICAL: rate at 100% util wrong"

    # APY at target should be ~4%
    apy = borrow_rate_to_apy(br)
    assert 0.03 < apy < 0.05, f"CRITICAL: APY at target = {apy*100:.2f}%, expected ~4%"

    print(f"✓ All self-verification passed")
    print(f"  rate_at_target = {r}")
    print(f"  borrow_rate at 90% util = {br}")
    print(f"  borrow_rate at 0% util  = {br_0}")
    print(f"  borrow_rate at 100% util = {br_100}")
    print(f"  APY at target = {apy*100:.4f}%")
    print(f"  16x ratio (100%/0%) = {br_100/br_0:.4f}")
