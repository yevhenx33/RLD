import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import AppShell from "./AppShell";
import LoadingScreen from "./LoadingScreen";
import { useEnvioStatus } from "../hooks/queries/useEnvioStatus";
import { SimulationProvider, useSim } from "../context/SimulationContext";

const HomepagePage = lazy(() => import("../pages/public/HomepagePage"));
const IntelPage = lazy(() => import("../pages/public/IntelPage"));
const BondsPage = lazy(() => import("../pages/app/BondsPage"));
const BondsDirectoryPage = lazy(() => import("../pages/app/BondsDirectoryPage"));
const ProtocolMarketsPage = lazy(
  () => import("../features/explore/pages/ProtocolMarketsPage"),
);
const LendingPoolPage = lazy(
  () => import("../pages/app/LendingPoolPage"),
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
const LendingDataPage = lazy(() => import("../pages/app/LendingDataPage"));

function LegacyExploreProtocolRedirect() {
  const { protocol } = useParams();
  return <Navigate to={`/data/${protocol}`} replace />;
}

function LegacyExploreMarketRedirect() {
  const { protocol, marketId } = useParams();
  return <Navigate to={`/data/${protocol}/${marketId}`} replace />;
}

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

function AnalyticsShell() {
  return <AppShell ratesLoaded isCapped={false} />;
}

function SimulationRuntimeShellInner() {
  const { ratesLoaded, isCapped } = useEnvioStatus();
  const { marketInfo } = useSim();
  return (
    <AppShell
      ratesLoaded={ratesLoaded}
      isCapped={isCapped}
      marketInfo={marketInfo}
      faucetEnabled
    />
  );
}

function SimulationRuntimeShell() {
  return (
    <SimulationProvider pollInterval={2000}>
      <SimulationRuntimeShellInner />
    </SimulationProvider>
  );
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<PublicShell />}>
        <Route path="/" element={renderLazy(HomepagePage)} />
        <Route path="/intel" element={renderLazy(IntelPage)} />
      </Route>

      <Route element={<AnalyticsShell />}>
        <Route path="/data" element={renderLazy(LendingDataPage)} />
        <Route
          path="/data/:protocol"
          element={renderLazy(ProtocolMarketsPage)}
        />
        <Route
          path="/data/:protocol/:marketId"
          element={renderLazy(LendingPoolPage)}
        />
      </Route>

      <Route element={<SimulationRuntimeShell />}>
        <Route path="/bonds" element={renderLazy(BondsDirectoryPage)} />
        <Route path="/bonds/:address" element={renderLazy(BondsPage)} />
        <Route
          path="/explore"
          element={<Navigate to="/data" replace />}
        />
        <Route
          path="/explore/:protocol/:marketId"
          element={<LegacyExploreMarketRedirect />}
        />
        <Route
          path="/explore/:protocol"
          element={<LegacyExploreProtocolRedirect />}
        />
        <Route
          path="/brokers/*"
          element={<Navigate to="/data" replace />}
        />
        <Route
          path="/strategies/*"
          element={<Navigate to="/data" replace />}
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
      </Route>
    </Routes>
  );
}
