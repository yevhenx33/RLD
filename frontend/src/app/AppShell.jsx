import { Outlet } from "react-router-dom";
import Header from "../components/layout/Header";

export default function AppShell({
  transparentHeader = false,
  ratesLoaded = true,
  isCapped = false,
  marketInfo = null,
  faucetEnabled = false,
}) {
  return (
    <>
      <Header
        transparent={transparentHeader}
        ratesLoaded={ratesLoaded}
        isCapped={isCapped}
        marketInfo={marketInfo}
        faucetEnabled={faucetEnabled}
      />
      <Outlet />
    </>
  );
}
