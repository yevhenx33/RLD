import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App";
import "./index.css";
import { WalletProvider } from './context/WalletContext.jsx';
import Layout from "./components/Layout";

// Lazy Load Secondary Pages
const Bonds = lazy(() => import("./components/Bonds"));
const Markets = lazy(() => import("./components/Markets"));
const Research = lazy(() => import("./components/Research"));
const Article = lazy(() => import("./components/Article"));

const Loading = () => (
  <div className="h-screen w-full flex items-center justify-center bg-black text-gray-500 font-mono text-xs animate-pulse">
    LOADING_MODULE...
  </div>
);

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <WalletProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<App />} />
            <Route
              path="/bonds"
              element={
                <Suspense fallback={<Loading />}>
                  <Bonds />
                </Suspense>
              }
            />
            <Route
              path="/markets"
              element={
                <Suspense fallback={<Loading />}>
                  <Markets />
                </Suspense>
              }
            />
            <Route
              path="/research"
              element={
                <Suspense fallback={<Loading />}>
                  <Research />
                </Suspense>
              }
            />
            <Route
              path="/research/:id"
              element={
                <Suspense fallback={<Loading />}>
                  <Article />
                </Suspense>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </WalletProvider>
  </React.StrictMode>
);
