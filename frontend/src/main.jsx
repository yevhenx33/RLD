import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import AppProviders from "./app/providers";
import AppRoutes from "./app/routes";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <AppProviders>
          <AppRoutes />
      </AppProviders>
    </BrowserRouter>
  </React.StrictMode>,
);
