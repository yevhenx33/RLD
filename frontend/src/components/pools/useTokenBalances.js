import { useCallback, useEffect, useState } from "react";
import { ethers } from "ethers";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";
import { rpcProvider } from "../../utils/provider";

const ERC20_BALANCE_ABI = ["function balanceOf(address) view returns (uint256)"];

export function useTokenBalances({ activeAddress, token0Addr, token1Addr }) {
  const [token0Balance, setToken0Balance] = useState(null);
  const [token1Balance, setToken1Balance] = useState(null);

  const refreshBalances = useCallback(async () => {
    if (!activeAddress || !token0Addr || !token1Addr) {
      setToken0Balance(null);
      setToken1Balance(null);
      return;
    }

    try {
      const provider = rpcProvider;
      const token0 = new ethers.Contract(token0Addr, ERC20_BALANCE_ABI, provider);
      const token1 = new ethers.Contract(token1Addr, ERC20_BALANCE_ABI, provider);
      const [balance0, balance1] = await Promise.all([
        token0.balanceOf(activeAddress),
        token1.balanceOf(activeAddress),
      ]);
      setToken0Balance(parseFloat(ethers.formatUnits(balance0, 6)));
      setToken1Balance(parseFloat(ethers.formatUnits(balance1, 6)));
    } catch (error) {
      console.warn("Balance fetch failed:", error);
    }
  }, [activeAddress, token0Addr, token1Addr]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshBalances();
    const id = setInterval(refreshBalances, REFRESH_INTERVALS.LP_BALANCE_MS);
    return () => clearInterval(id);
  }, [refreshBalances]);

  return { token0Balance, token1Balance, refreshBalances };
}
