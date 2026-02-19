import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import App from "./App";
import "./index.css";
import { WalletProvider } from "./context/WalletContext.jsx";
import Layout from "./components/Layout";

// Lazy Load Secondary Pages
const Bonds = lazy(() => import("./components/Bonds"));
const Markets = lazy(() => import("./components/Markets"));

const Portfolio = lazy(() => import("./components/Portfolio"));
const SimulationTerminal = lazy(
  () => import("./components/SimulationTerminal"),
);
const Homepage = lazy(() => import("./components/Homepage"));

const PoolLP = lazy(() => import("./components/PoolLP"));
const PoolsDirectory = lazy(() => import("./components/PoolsDirectory"));
const PerpsDirectory = lazy(() => import("./components/PerpsDirectory"));
const BondsDirectory = lazy(() => import("./components/BondsDirectory"));

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
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route
                path="/"
                element={
                  <Suspense fallback={<Loading />}>
                    <Homepage />
                  </Suspense>
                }
              />
              <Route path="/app" element={<App />} />
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
            </Route>
          </Routes>
        </BrowserRouter>
      </WalletProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
