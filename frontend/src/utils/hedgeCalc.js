/**
 * hedgeCalc.js — Bond hedge size calculator.
 *
 * Simplified formula (V1):
 *   Q = notional × (rate / 100) × (durationHours / 8760)
 *
 * The hedge quantity represents the wRLP to sell via TWAMM over the bond
 * duration to lock in the fixed yield.  The linear unwind converts floating
 * interest exposure into a known fixed rate.
 */

/**
 * Calculate the wRLP hedge size for a synthetic bond.
 *
 * @param {number} notionalUSD   Bond notional in USD (e.g. 1000)
 * @param {number} ratePercent   Entry rate / APY (e.g. 5.25 for 5.25%)
 * @param {number} durationHours Bond duration in hours
 * @returns {number}             Hedge quantity in position-token units
 */
export function calcHedgeSize(notionalUSD, ratePercent, durationHours) {
  if (!notionalUSD || !ratePercent || !durationHours) return 0;
  return notionalUSD * (ratePercent / 100) * (durationHours / 8760);
}

/**
 * Estimate initial LTV after opening a short hedge.
 *
 * LTV = hedgeDebt / (notional + estimated proceeds) × 100
 * Assumes proceeds ≈ hedgeDebt (1:1 for rate tokens near par).
 *
 * @param {number} notionalUSD   Initial collateral in USD
 * @param {number} hedgeSize     wRLP hedge size
 * @returns {number}             LTV percentage (e.g. 33.3)
 */
export function calcInitialLTV(notionalUSD, hedgeSize) {
  if (!notionalUSD || !hedgeSize) return 0;
  const totalCollateral = notionalUSD + hedgeSize; // notional + estimated proceeds
  return (hedgeSize / totalCollateral) * 100;
}
