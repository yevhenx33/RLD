import { useState, useMemo } from "react";

/**
 * Get the next whole-hour epoch boundary from now.
 * JTM orders start at the next 1-hour epoch after submission.
 */
function getNextEpoch() {
  const d = new Date();
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  return d;
}

/**
 * Format a Date to "YYYY-MM-DDTHH:00" for datetime-local inputs.
 */
function toDateTimeLocal(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
}

/**
 * Format a Date for display: "DD/MM/YYYY HH:00"
 */
function formatEpoch(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:00`;
}

export function useTradeLogic() {
  const [activeProduct, setActiveProduct] = useState("FIXED_YIELD");
  const [activeTab, setActiveTab] = useState("OPEN");

  const [notional, setNotional] = useState(1000);
  const [maturityHours, setMaturityHours] = useState(90 * 24); // default 90 days in hours
  const [slippage, setSlippage] = useState(0.5);

  // Derived: epoch start = next whole hour, epoch end = start + duration
  const epochs = useMemo(() => {
    const start = getNextEpoch();
    const end = new Date(start.getTime() + maturityHours * 3600 * 1000);
    return {
      start,
      end,
      startDisplay: formatEpoch(start),
      endDisplay: formatEpoch(end),
      endDateTimeLocal: toDateTimeLocal(end),
    };
  }, [maturityHours]);

  const maturityDays = maturityHours / 24;

  const handleHoursChange = (hours) => {
    setMaturityHours(Math.max(1, Math.min(8760, hours)));
  };

  const handleDaysChange = (days) => {
    setMaturityHours(days * 24);
  };

  const handleEndDateChange = (dateTimeStr) => {
    // Compute hours from epoch start to selected end
    const start = getNextEpoch();
    const end = new Date(dateTimeStr);
    const diffHours = Math.round((end - start) / (3600 * 1000));
    setMaturityHours(Math.max(1, Math.min(8760, diffHours)));
  };

  return {
    state: {
      activeProduct,
      activeTab,
      notional,
      maturityHours,
      maturityDays,
      epochs,
      slippage,
    },
    actions: {
      setActiveProduct,
      setActiveTab,
      setNotional,
      handleHoursChange,
      handleDaysChange,
      handleEndDateChange,
      setSlippage,
    },
  };
}
