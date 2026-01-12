import axios from "axios";

export const API_URL = import.meta.env.VITE_API_BASE_URL || "https://rate-dashboard.onrender.com";

const API_KEY = import.meta.env.VITE_API_KEY;
const authHeaders = API_KEY ? { "X-API-Key": API_KEY } : {};

export const fetcher = (url) => axios.get(url, { headers: authHeaders }).then((res) => res.data);

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
