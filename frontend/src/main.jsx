import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App";
import Bonds from "./components/Bonds";
import Markets from "./components/Markets";
import Research from "./components/Research";
import Article from "./components/Article";
import "./index.css";
import { WalletProvider } from './context/WalletContext.jsx';

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <WalletProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/bonds" element={<Bonds />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Article />} />
        </Routes>
      </BrowserRouter>
    </WalletProvider>
  </React.StrictMode>
);
