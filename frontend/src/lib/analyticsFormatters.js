export const finiteNumber = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

export const hasAnyFiniteValue = (point, keys) => {
  return keys.some((key) => Number.isFinite(Number(point?.[key])));
};

export const formatCurrency = (value) => {
  const amount = finiteNumber(value);
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(2)}M`;
  if (amount >= 1e3) return `$${(amount / 1e3).toFixed(0)}K`;
  return `$${amount.toFixed(0)}`;
};

export const formatCompactUsd = (value) => {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(1)}M`;
  if (amount >= 1e3) return `$${(amount / 1e3).toFixed(1)}K`;
  return `$${amount.toFixed(0)}`;
};


export const formatUsdPrice = (value) => {
  const price = finiteNumber(value);
  if (price >= 1000) return formatCurrency(price);
  if (price >= 1) return `$${price.toFixed(4)}`;
  if (price > 0) return `$${price.toPrecision(4)}`;
  return "$0.00";
};

export const shortAddress = (value, emptyValue = "") => {
  const raw = String(value || "");
  if (!raw) return emptyValue;
  return raw.length > 12 ? `${raw.slice(0, 6)}...${raw.slice(-4)}` : raw;
};

export const shortOrUnassignedAddress = (value) => {
  const raw = String(value || "");
  if (!raw || /^0x0{40}$/i.test(raw)) return "Unassigned";
  return shortAddress(raw);
};

export const filterHistoryByWindow = (rows, days) => {
  if (!days || !rows.length) return rows;
  const latestTimestamp = rows.reduce(
    (latest, row) => Math.max(latest, finiteNumber(row.timestamp)),
    0,
  );
  if (latestTimestamp <= 0) return rows;
  const minTimestamp = latestTimestamp - days * 24 * 60 * 60;
  return rows.filter((row) => finiteNumber(row.timestamp) >= minTimestamp);
};

export const proportionalSlots = (items, totals, top, bottom, height, minHeight = 14, gap = 12) => {
  const slots = new Map();
  if (!items.length) return slots;
  const total = items.reduce((sum, item) => sum + finiteNumber(totals.get(item)), 0);
  const available = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawHeights = items.map((item) => (
    total > 0 ? (available * finiteNumber(totals.get(item))) / total : available / items.length
  ));
  const heights = rawHeights.map((heightValue) => Math.max(minHeight, heightValue));
  const used = heights.reduce((sum, heightValue) => sum + heightValue, 0) + gap * (items.length - 1);
  const scale = used > available ? available / used : 1;
  let y = top + Math.max(0, (height - top - bottom - used * scale) / 2);
  items.forEach((item, index) => {
    const h = Math.max(8, heights[index] * scale);
    slots.set(item, { y, h, center: y + h / 2, total: finiteNumber(totals.get(item)) });
    y += h + gap * scale;
  });
  return slots;
};

export const formatApy = (value) => `${(finiteNumber(value) * 100).toFixed(2)}%`;

export const formatPercent = (value, digits = 2) => `${(finiteNumber(value) * 100).toFixed(digits)}%`;

export const formatLltvRange = (market) => {
  const min = finiteNumber(market?.lltvMin, NaN);
  const max = finiteNumber(market?.lltvMax, NaN);
  if (Number.isFinite(min) && Number.isFinite(max) && max > 0) {
    if (Math.abs(max - min) < 0.000001) return formatPercent(max);
    return `${formatPercent(min)}-${formatPercent(max)}`;
  }
  const lltv = finiteNumber(market?.lltv, NaN);
  return Number.isFinite(lltv) && lltv > 0 ? formatPercent(lltv) : "—";
};

export const formatOptionalCurrency = (value) => {
  const amount = finiteNumber(value);
  return amount > 0 ? formatCurrency(amount) : "-";
};

export const etherscanAddressUrl = (value) => {
  const address = String(value || "");
  return address.startsWith("0x") ? `https://etherscan.io/address/${address}` : null;
};

export const formatOracleProvider = (value, emptyValue = "-") => {
  if (!value) return emptyValue;
  const label = String(value)
    .replace(/_/g, " ")
    .replace(/supported/i, "")
    .trim()
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
  return label || "Unknown";
};
