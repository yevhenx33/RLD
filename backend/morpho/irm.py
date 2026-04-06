"""
Morpho Blue AdaptiveCurveIRM — Faithful Python Port.

This module is a 1:1 translation of the Solidity contracts:
  - AdaptiveCurveIrm.sol (main logic)
  - ConstantsLib.sol     (protocol constants)
  - MathLib.sol          (WAD arithmetic)
  - ExpLib.sol           (wExp approximation)
  - UtilsLib.sol         (bound/clamp)

All arithmetic uses Python ints to exactly replicate Solidity's
int256/uint256 WAD-scaled fixed-point math. No floats in the core path.

References:
  https://github.com/morpho-org/morpho-blue-irm/tree/main/src/adaptive-curve-irm
"""

from __future__ import annotations
import math

# ═══════════════════════════════════════════════════════════════════
#  Constants  (ConstantsLib.sol)
# ═══════════════════════════════════════════════════════════════════

WAD: int = 10**18
SECONDS_PER_YEAR: int = 365 * 24 * 3600  # 31_536_000 (non-leap)
SECONDS_PER_DAY: int = 86_400
DAYS_PER_YEAR: int = 365

# Curve steepness = 4 (scaled by WAD)
CURVE_STEEPNESS: int = 4 * WAD

# Target utilization = 90%
TARGET_UTILIZATION: int = 9 * WAD // 10  # 0.9 ether

# Adjustment speed = 50/year (per-second, scaled by WAD)
# Solidity: 50 ether / int256(365 days)  →  365 * 86400 = 31_536_000
ADJUSTMENT_SPEED: int = 50 * WAD // (DAYS_PER_YEAR * SECONDS_PER_DAY)

# Initial rate at target = 4%/year (per-second, WAD-scaled)
INITIAL_RATE_AT_TARGET: int = (4 * WAD // 100) // (DAYS_PER_YEAR * SECONDS_PER_DAY)

# Min rate at target = 0.1%/year
MIN_RATE_AT_TARGET: int = (WAD // 1000) // (DAYS_PER_YEAR * SECONDS_PER_DAY)

# Max rate at target = 200%/year
MAX_RATE_AT_TARGET: int = (2 * WAD) // (DAYS_PER_YEAR * SECONDS_PER_DAY)


# ═══════════════════════════════════════════════════════════════════
#  ExpLib constants
# ═══════════════════════════════════════════════════════════════════

LN_2_INT: int = 693147180559945309  # ln(2) * 1e18 (truncated)

# ln(1e-18) * 1e18  ≈ -41.4465... * 1e18
LN_WEI_INT: int = -41_446531673892822312

# Upper bound: ln(type(int256).max / 1e36) * 1e18 (floored)
WEXP_UPPER_BOUND: int = 93_859467695000404319

# Value of wExp(WEXP_UPPER_BOUND)
WEXP_UPPER_VALUE: int = 57716089161558943949701069502944508345128_422502756744429568


# ═══════════════════════════════════════════════════════════════════
#  MathLib  (signed WAD arithmetic, round toward zero)
# ═══════════════════════════════════════════════════════════════════

def w_mul_to_zero(x: int, y: int) -> int:
    """(x * y) / WAD  rounded toward zero — matches Solidity's signed integer division."""
    return int_div_toward_zero(x * y, WAD)


def w_div_to_zero(x: int, y: int) -> int:
    """(x * WAD) / y  rounded toward zero."""
    return int_div_toward_zero(x * WAD, y)


def int_div_toward_zero(a: int, b: int) -> int:
    """Signed integer division rounding toward zero (Solidity semantics).

    Python's // rounds toward negative infinity, but Solidity rounds toward zero.
    This function replicates Solidity's behavior using pure integer arithmetic
    to avoid float precision loss for large int256 values.
    """
    if b == 0:
        raise ZeroDivisionError("division by zero")
    # Python's divmod: q*b + r == a, with r having the same sign as b.
    # For truncation toward zero: if signs differ and remainder != 0, adjust.
    q, r = divmod(a, b)
    # divmod rounds toward -inf. To round toward zero when signs differ:
    if r != 0 and (a ^ b) < 0:
        q += 1
    return q


# ═══════════════════════════════════════════════════════════════════
#  ExpLib.wExp  (2nd-order Taylor approximation)
# ═══════════════════════════════════════════════════════════════════

def w_exp(x: int) -> int:
    """Approximate exp(x/WAD) * WAD using the Solidity ExpLib algorithm.

    Decomposes x = q * ln(2) + r, uses 2nd-order Taylor for e^r,
    then returns 2^q * e^r.
    """
    if x < LN_WEI_INT:
        return 0
    if x >= WEXP_UPPER_BOUND:
        return WEXP_UPPER_VALUE

    # q = x / ln(2), rounded half toward zero
    rounding_adj = -LN_2_INT // 2 if x < 0 else LN_2_INT // 2
    q = int_div_toward_zero(x + rounding_adj, LN_2_INT)
    r = x - q * LN_2_INT

    # 2nd-order Taylor: e^r ≈ 1 + r + r²/2
    exp_r = WAD + r + int_div_toward_zero(r * r, WAD) // 2

    # Return 2^q * e^r via bit shifts
    if q >= 0:
        return exp_r << q
    else:
        return exp_r >> (-q)


# ═══════════════════════════════════════════════════════════════════
#  UtilsLib.bound
# ═══════════════════════════════════════════════════════════════════

def bound(x: int, low: int, high: int) -> int:
    """Clamp x between low and high. Assumes low <= high."""
    return max(low, min(x, high))


# ═══════════════════════════════════════════════════════════════════
#  AdaptiveCurveIrm — Core Logic
# ═══════════════════════════════════════════════════════════════════

def curve(rate_at_target: int, err: int) -> int:
    """The piecewise-linear rate curve.

    r = ((1 - 1/C) * err + 1) * rateAtTarget   if err < 0
        ((C - 1)   * err + 1) * rateAtTarget   if err >= 0

    Where C = CURVE_STEEPNESS.
    """
    if err < 0:
        coeff = WAD - w_div_to_zero(WAD, CURVE_STEEPNESS)  # 1 - 1/C
    else:
        coeff = CURVE_STEEPNESS - WAD  # C - 1

    return w_mul_to_zero(w_mul_to_zero(coeff, err) + WAD, rate_at_target)


def compute_err(utilization_wad: int) -> int:
    """Compute the normalized utilization error.

    err = (utilization - TARGET_UTILIZATION) / errNormFactor
    where errNormFactor = (1 - TARGET) if util > TARGET else TARGET
    """
    if utilization_wad > TARGET_UTILIZATION:
        err_norm_factor = WAD - TARGET_UTILIZATION
    else:
        err_norm_factor = TARGET_UTILIZATION
    return w_div_to_zero(utilization_wad - TARGET_UTILIZATION, err_norm_factor)


def new_rate_at_target(start_rate: int, linear_adaptation: int) -> int:
    """Compute updated rateAtTarget after adaptive adjustment.

    Formula: clamp(start * exp(linearAdaptation), MIN, MAX)
    """
    return bound(
        w_mul_to_zero(start_rate, w_exp(linear_adaptation)),
        MIN_RATE_AT_TARGET,
        MAX_RATE_AT_TARGET,
    )


def compute_borrow_rate(rate_at_target_val: int, utilization_wad: int) -> int:
    """Compute the instantaneous per-second borrow rate.

    This is the core IRM function: applies the curve to rateAtTarget
    based on the normalized utilization error.

    Returns: borrow_rate as WAD-scaled int (per second).
    """
    err = compute_err(utilization_wad)
    return curve(rate_at_target_val, err)


def borrow_rate_to_apy(rate_per_second_wad: int) -> float:
    """Convert a WAD-scaled per-second rate to annualized APY (float, as fraction).

    APY = exp(rate_per_second * SECONDS_PER_YEAR) - 1
    """
    rate_float = rate_per_second_wad / WAD
    return math.exp(rate_float * SECONDS_PER_YEAR) - 1.0


def compute_supply_apy(borrow_apy: float, utilization: float, fee_wad: int) -> float:
    """Supply APY = borrow_apy * utilization * (1 - fee).

    Args:
        borrow_apy: as a fraction (e.g. 0.04 = 4%)
        utilization: as a fraction (0.0 to 1.0)
        fee_wad: WAD-scaled fee (e.g. 0 for no fee)
    """
    fee_frac = fee_wad / WAD
    return borrow_apy * utilization * (1.0 - fee_frac)


def compute_full_apy(
    rate_at_target_val: int,
    utilization_wad: int,
    fee_wad: int,
) -> tuple[float | None, float | None]:
    """End-to-end: from (rateAtTarget, utilization, fee) → (borrow_apy, supply_apy).

    Returns (None, None) if rate_at_target_val is None or zero.
    """
    if rate_at_target_val is None or rate_at_target_val <= 0:
        return None, None
    if utilization_wad is None:
        return None, None

    borrow_rate = compute_borrow_rate(rate_at_target_val, utilization_wad)
    borrow_apy = borrow_rate_to_apy(borrow_rate)

    utilization_frac = utilization_wad / WAD
    supply_apy = compute_supply_apy(borrow_apy, utilization_frac, fee_wad)

    return borrow_apy, supply_apy


# ═══════════════════════════════════════════════════════════════════
#  Adaptive Rate Evolution (backward/forward extrapolation)
# ═══════════════════════════════════════════════════════════════════

def evolve_rate_at_target(
    start_rate: int,
    utilization_wad: int,
    elapsed_seconds: int,
) -> int:
    """Evolve rateAtTarget forward by `elapsed_seconds`.

    This replicates the adaptive mechanism from _borrowRate:
      speed = ADJUSTMENT_SPEED * err
      linearAdaptation = speed * elapsed
      endRate = clamp(startRate * exp(linearAdaptation), MIN, MAX)
    """
    if elapsed_seconds <= 0:
        return start_rate

    err = compute_err(utilization_wad)
    speed = w_mul_to_zero(ADJUSTMENT_SPEED, err)
    linear_adaptation = speed * elapsed_seconds

    if linear_adaptation == 0:
        return start_rate

    return new_rate_at_target(start_rate, linear_adaptation)


# ═══════════════════════════════════════════════════════════════════
#  Utilization Regime Classification
# ═══════════════════════════════════════════════════════════════════

# Regime labels and boundaries (utilization as float 0-1)
REGIME_THRESHOLDS: list[tuple[float, str]] = [
    (1.00, "trapped"),      # exactly 100%
    (0.95, "critical"),     # 95% - 100%
    (0.90, "elevated"),     # 90% - 95% (above IRM target)
    (0.50, "normal"),       # 50% - 90%
    (0.00, "low"),          # 0%  - 50%
]


def classify_utilization_regime(utilization: float) -> str:
    """Classify utilization into a named regime.

    Args:
        utilization: float between 0.0 and 1.0

    Returns:
        One of: 'trapped', 'critical', 'elevated', 'normal', 'low', 'idle'
    """
    if utilization >= 1.0:
        return "trapped"
    if utilization >= 0.95:
        return "critical"
    if utilization >= 0.90:
        return "elevated"
    if utilization >= 0.50:
        return "normal"
    if utilization > 0.0:
        return "low"
    return "idle"
