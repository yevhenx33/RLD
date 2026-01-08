import { useState } from "react";
import { getFutureDate, getDaysDiff } from "../utils/helpers";

export function useTradeLogic(currentRate) {
  const [activeProduct, setActiveProduct] = useState("FIXED_YIELD");
  const [activeTab, setActiveTab] = useState("OPEN");

  const [notional, setNotional] = useState(1000);
  const [maturityDays, setMaturityDays] = useState(90);
  const [maturityDate, setMaturityDate] = useState(getFutureDate(90));
  const [slippage, setSlippage] = useState(0.5);

  const handleDaysChange = (days) => {
    setMaturityDays(days);
    setMaturityDate(getFutureDate(days));
  };

  const handleDateChange = (date) => {
    setMaturityDate(date);
    setMaturityDays(getDaysDiff(date));
  };

  return {
    state: {
      activeProduct,
      activeTab,
      notional,
      maturityDays,
      maturityDate,
      slippage,
    },
    actions: {
      setActiveProduct,
      setActiveTab,
      setNotional,
      handleDaysChange,
      handleDateChange,
      setSlippage,
    },
  };
}
