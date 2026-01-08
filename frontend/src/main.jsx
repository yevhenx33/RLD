import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App";
import Bonds from "./components/Bonds";
import "./index.css";
import { WalletProvider } from './context/WalletContext.jsx';

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <WalletProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/bonds" element={<Bonds />} />
        </Routes>
      </BrowserRouter>
    </WalletProvider>
  </React.StrictMode>
);
