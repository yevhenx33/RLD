import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./index.css";
import { WalletProvider } from "./context/WalletContext.jsx";
import { SimulationProvider } from "./context/SimulationContext.jsx";
import Layout from "./components/layout/Layout";

// Lazy Load All Pages

const Homepage = lazy(() => import("./components/landing/Homepage"));
const Bonds = lazy(() => import("./components/bonds/Bonds"));
const BondsDirectory = lazy(() => import("./components/bonds/BondsDirectory"));
const Markets = lazy(() => import("./components/charts/Markets"));
const ProtocolMarkets = lazy(() => import("./components/charts/ProtocolMarkets"));
const MarketDetail = lazy(() => import("./components/charts/MarketDetail"));
const Portfolio = lazy(() => import("./components/portfolio/Portfolio"));
const SimulationTerminal = lazy(() => import("./components/trading/SimulationTerminal"));
const PerpsDirectory = lazy(() => import("./components/trading/PerpsDirectory"));
const CdsDirectory = lazy(() => import("./components/cds/CdsDirectory"));
const Cds = lazy(() => import("./components/cds/Cds"));
const PoolLP = lazy(() => import("./components/pools/PoolLP"));
const PoolsDirectory = lazy(() => import("./components/pools/PoolsDirectory"));
const TwammOrders = lazy(() => import("./components/twamm/TwammOrders"));
const Vaults = lazy(() => import("./components/common/Vaults"));
const VaultDetail = lazy(() => import("./components/common/VaultDetail"));
const BasisTrade = lazy(() => import("./components/basis-trade/BasisTrade"));
const BrokersDashboard = lazy(() => import("./components/brokers/BrokersDashboard"));
const IntelDashboard = lazy(() => import("./components/intel/IntelDashboard"));

// eslint-disable-next-line react-refresh/only-export-components
const Loading = () => (
  <div className="h-screen w-full flex items-center justify-center bg-black text-gray-500 font-mono text-xs animate-pulse">
    LOADING_MODULE...
  </div>
);

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("React Error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-full flex flex-col items-center justify-center bg-black text-red-500 font-mono p-10">
          <h1 className="text-xl font-bold mb-4">APPLICATION_CRASHED</h1>
          <pre className="text-xs bg-gray-900 p-4 border border-red-900 rounded break-all whitespace-pre-wrap max-w-full">
            {this.state.error?.toString()}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary>
      <WalletProvider>
        <SimulationProvider>
        <BrowserRouter>
          <Routes>
            <Route
              path="/"
              element={
                <Suspense fallback={<Loading />}>
                  <Homepage />
                </Suspense>
              }
            />
            <Route
              path="/intel"
              element={
                <Suspense fallback={<Loading />}>
                  <IntelDashboard />
                </Suspense>
              }
            />
            <Route element={<Layout />}>
              <Route
                path="/brokers"
                element={
                  <Suspense fallback={<Loading />}>
                    <BrokersDashboard />
                  </Suspense>
                }
              />

              <Route
                path="/bonds"
                element={
                  <Suspense fallback={<Loading />}>
                    <BondsDirectory />
                  </Suspense>
                }
              />
              <Route
                path="/bonds/:address"
                element={
                  <Suspense fallback={<Loading />}>
                    <Bonds />
                  </Suspense>
                }
              />
              <Route
                path="/explore"
                element={
                  <Suspense fallback={<Loading />}>
                    <Markets />
                  </Suspense>
                }
              />
              <Route
                path="/explore/:protocol"
                element={
                  <Suspense fallback={<Loading />}>
                    <ProtocolMarkets />
                  </Suspense>
                }
              />
              <Route
                path="/explore/:protocol/:marketId"
                element={
                  <Suspense fallback={<Loading />}>
                    <MarketDetail />
                  </Suspense>
                }
              />
              <Route
                path="/portfolio"
                element={
                  <Suspense fallback={<Loading />}>
                    <Portfolio />
                  </Suspense>
                }
              />
              <Route
                path="/markets"
                element={<Navigate to="/markets/perps" replace />}
              />
              <Route
                path="/markets/perps"
                element={
                  <Suspense fallback={<Loading />}>
                    <PerpsDirectory />
                  </Suspense>
                }
              />
              <Route
                path="/markets/perps/:address"
                element={
                  <Suspense fallback={<Loading />}>
                    <SimulationTerminal />
                  </Suspense>
                }
              />
              <Route
                path="/markets/cds"
                element={
                  <Suspense fallback={<Loading />}>
                    <CdsDirectory />
                  </Suspense>
                }
              />
              <Route
                path="/markets/cds/:address"
                element={
                  <Suspense fallback={<Loading />}>
                    <Cds />
                  </Suspense>
                }
              />
              <Route
                path="/markets/pools"
                element={
                  <Suspense fallback={<Loading />}>
                    <PoolsDirectory />
                  </Suspense>
                }
              />
              <Route
                path="/markets/pools/:address"
                element={
                  <Suspense fallback={<Loading />}>
                    <PoolLP />
                  </Suspense>
                }
              />
              <Route
                path="/markets/twamm"
                element={
                  <Suspense fallback={<Loading />}>
                    <TwammOrders />
                  </Suspense>
                }
              />
              <Route
                path="/strategies"
                element={
                  <Suspense fallback={<Loading />}>
                    <Vaults />
                  </Suspense>
                }
              />
              <Route
                path="/strategies/basis-trade"
                element={
                  <Suspense fallback={<Loading />}>
                    <BasisTrade />
                  </Suspense>
                }
              />
              <Route
                path="/strategies/:id"
                element={
                  <Suspense fallback={<Loading />}>
                    <VaultDetail />
                  </Suspense>
                }
              />
            </Route>
          </Routes>
        </BrowserRouter>
        </SimulationProvider>
      </WalletProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
