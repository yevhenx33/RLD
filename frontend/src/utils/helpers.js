import axios from "axios";

export const API_BASE =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
export const API_URL = API_BASE;

// API auth is handled server-side by Nginx proxy — no client-side key needed
export const authHeaders = {};

export const ENVIO_GQL_URL = "/envio-graphql";

// Earliest date the indexer has data for (protocol deployment date)
export const DEPLOYMENT_DATE = "2026-03-03";

export const fetcher = (url) => axios.get(url).then((res) => res.data);

export const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};

export const getFutureDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().split("T")[0];
};

export const getDaysDiff = (dateStr) => {
  const d1 = new Date();
  const d2 = new Date(dateStr);
  const diffTime = Math.abs(d2 - d1);
  return Math.ceil(diffTime / (1000 * 60 * 60 * 24));
};

export const getToday = () => new Date().toISOString().split("T")[0];

export const formatNum = (num, digits = 2, symbol = "") => {
  if (num === null || num === undefined) return "--";

  return `${symbol}${num.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};
