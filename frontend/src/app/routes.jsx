import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./AppShell";
import LoadingScreen from "./LoadingScreen";
import { useEnvioStatus } from "../hooks/queries/useEnvioStatus";

const HomepagePage = lazy(() => import("../pages/public/HomepagePage"));
const IntelPage = lazy(() => import("../pages/public/IntelPage"));
const BrokersPage = lazy(() => import("../pages/app/BrokersPage"));
const BondsPage = lazy(() => import("../pages/app/BondsPage"));
const BondsDirectoryPage = lazy(() => import("../pages/app/BondsDirectoryPage"));
const MarketsPage = lazy(() => import("../features/explore/pages/MarketsPage"));
const ProtocolMarketsPage = lazy(
  () => import("../features/explore/pages/ProtocolMarketsPage"),
);
const MarketDetailPage = lazy(
  () => import("../features/explore/pages/MarketDetailPage"),
);
const PortfolioPage = lazy(() => import("../pages/app/PortfolioPage"));
const SimulationTerminalPage = lazy(
  () => import("../pages/app/SimulationTerminalPage"),
);
const PerpsDirectoryPage = lazy(() => import("../pages/app/PerpsDirectoryPage"));
const CdsDirectoryPage = lazy(() => import("../pages/app/CdsDirectoryPage"));
const CdsPage = lazy(() => import("../pages/app/CdsPage"));
const PoolLPPage = lazy(() => import("../pages/app/PoolLPPage"));
const PoolsDirectoryPage = lazy(() => import("../pages/app/PoolsDirectoryPage"));
const TwammOrdersPage = lazy(() => import("../pages/app/TwammOrdersPage"));
const StrategiesPage = lazy(() => import("../pages/app/StrategiesPage"));
const BasisTradePage = lazy(() => import("../pages/app/BasisTradePage"));
const StrategyDetailPage = lazy(() => import("../pages/app/StrategyDetailPage"));

function renderLazy(component) {
  const LazyComponent = component;
  return (
    <Suspense fallback={<LoadingScreen />}>
      <LazyComponent />
    </Suspense>
  );
}

function PublicShell() {
  return <AppShell transparentHeader ratesLoaded isCapped={false} />;
}

function RuntimeShell() {
  const { ratesLoaded, isCapped } = useEnvioStatus();
  return <AppShell ratesLoaded={ratesLoaded} isCapped={isCapped} />;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<PublicShell />}>
        <Route path="/" element={renderLazy(HomepagePage)} />
        <Route path="/intel" element={renderLazy(IntelPage)} />
      </Route>

      <Route element={<RuntimeShell />}>
        <Route path="/brokers" element={renderLazy(BrokersPage)} />
        <Route path="/bonds" element={renderLazy(BondsDirectoryPage)} />
        <Route path="/bonds/:address" element={renderLazy(BondsPage)} />
        <Route path="/explore" element={renderLazy(MarketsPage)} />
        <Route
          path="/explore/:protocol"
          element={renderLazy(ProtocolMarketsPage)}
        />
        <Route
          path="/explore/:protocol/:marketId"
          element={renderLazy(MarketDetailPage)}
        />
        <Route path="/portfolio" element={renderLazy(PortfolioPage)} />
        <Route path="/markets" element={<Navigate to="/markets/perps" replace />} />
        <Route path="/markets/perps" element={renderLazy(PerpsDirectoryPage)} />
        <Route
          path="/markets/perps/:address"
          element={renderLazy(SimulationTerminalPage)}
        />
        <Route path="/markets/cds" element={renderLazy(CdsDirectoryPage)} />
        <Route path="/markets/cds/:address" element={renderLazy(CdsPage)} />
        <Route path="/markets/pools" element={renderLazy(PoolsDirectoryPage)} />
        <Route path="/markets/pools/:address" element={renderLazy(PoolLPPage)} />
        <Route path="/markets/twamm" element={renderLazy(TwammOrdersPage)} />
        <Route path="/strategies" element={renderLazy(StrategiesPage)} />
        <Route path="/strategies/basis-trade" element={renderLazy(BasisTradePage)} />
        <Route path="/strategies/:id" element={renderLazy(StrategyDetailPage)} />
      </Route>
    </Routes>
  );
}
