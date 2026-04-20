import React, { useState, useEffect } from "react";
import { Outlet } from "react-router-dom";
import Header from "./Header";
import { API_BASE, ENVIO_GQL_URL, authHeaders } from "../../utils/helpers";

export default function Layout() {
  const [headerData, setHeaderData] = useState({
    latest: { block_number: 0 },
    isCapped: false,
    ratesLoaded: false,
  });

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(ENVIO_GQL_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: `{ historicalRates(symbols: ["USDC"], resolution: "1D", limit: 1) { timestamp } }`
          }),
        });
        const json = await res.json();

        if (json?.data?.historicalRates) {
          setHeaderData({
            latest: { block_number: json.data.historicalRates[0]?.timestamp || 0 },
            isCapped: false, // Legacy header constraint parameter mapping
            ratesLoaded: true,
          });
        } else {
          setHeaderData((prev) => ({ ...prev, ratesLoaded: false }));
        }
      } catch (err) {
        console.error("Global Layout Status Fetch Error:", err);
        setHeaderData((prev) => ({ ...prev, ratesLoaded: false }));
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 15000); // Check every 15s
    return () => clearInterval(interval);
  }, []);

  return (
    <>
      <Header
        isCapped={headerData.isCapped}
        ratesLoaded={headerData.ratesLoaded}
      />
      <Outlet />
    </>
  );
}
