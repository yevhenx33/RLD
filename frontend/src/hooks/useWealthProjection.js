import { useMemo } from "react";

export function useWealthProjection(collateral, currentRate, days = 90) {
  return useMemo(() => {
    const data = [];
    const fixedRateDaily = currentRate / 100 / 365;
    const volatility = 0.02;
    const cycleSpeed = 0.1;

    for (let i = 0; i <= days; i++) {
      const fixedBalance = collateral * (1 + fixedRateDaily * i);
      const trend = collateral * (1 + fixedRateDaily * 1.2 * i);
      const wave = Math.sin(i * cycleSpeed) * (collateral * volatility);
      const noise = (Math.random() - 0.5) * (collateral * 0.005);
      const variableBalance = trend + wave + noise;

      data.push({
        day: i,
        fixed: fixedBalance,
        variable: variableBalance,
        label: `Day ${i}`,
      });
    }
    return data;
  }, [collateral, currentRate, days]);
}
