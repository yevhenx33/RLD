import React, { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import {
  Activity,
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  PieChart as PieChartIcon,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import {
  FLUID_PRODUCT_SNAPSHOTS_QUERY,
  FLUID_VAULT_COMPOSITION_HISTORY_QUERY,
  LENDING_DATA_QUERY,
  PROTOCOL_APY_HISTORY_QUERY,
  PROTOCOL_ASSET_APY_HISTORY_QUERY,
  PROTOCOL_MARKETS_QUERY,
} from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import { marketRouteFor } from "../../../lib/protocolConfig";
import {
  getProtocolIcon,
  getTokenColor,
  getTokenIcon,
} from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

const PROTOCOL_KEY = "FLUID_MARKET";
const DISPLAY_NAME = "Fluid";
const APY_RESOLUTION = "1D";
const APY_LIMIT = 5000;
const PAGE_SIZE = 15;
const COMPOSITION_MAX_ASSETS = 7;
const FLOW_WINDOWS = [
  { label: "1D", days: 1 },
  { label: "7D", days: 7 },
  { label: "1M", days: 30 },
];
const HISTORY_WINDOWS = [
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "1Y", days: 365 },
  { label: "ALL", days: null },
];
const TABLE_MODES = [
  { key: "vaults", label: "VAULTS" },
  { key: "markets", label: "MARKETS" },
  { key: "assets", label: "ASSETS" },
];
const VAULT_COLUMNS = [
  { key: "supplyUsd", label: "Supply, $" },
  { key: "borrowUsd", label: "Borrow, $" },
  { key: "withdrawableUsd", label: "Liquidity, $" },
  { key: "supplyApy", label: "Supply APY, %" },
  { key: "borrowApy", label: "Borrow APY, %" },
  { key: "utilization", label: "Utilization, %" },
  { key: "lltv", label: "LTV Range, %" },
];
const MARKET_COLUMNS = [
  { key: "supplyUsd", label: "Supply, $" },
  { key: "borrowUsd", label: "Borrow, $" },
  { key: "netWorth", label: "Liquidity, $" },
  { key: "supplyApy", label: "Supply APY, %" },
  { key: "borrowApy", label: "Borrow APY, %" },
  { key: "utilization", label: "Utilization, %" },
  { key: "lltv", label: "LTV, %" },
];
const ASSET_COLUMNS = [
  { key: "supplyUsd", label: "Supply, $" },
  { key: "borrowUsd", label: "Borrow, $" },
  { key: "netWorth", label: "Liquidity, $" },
  { key: "supplyApy", label: "Supply APY, %" },
  { key: "borrowApy", label: "Borrow APY, %" },
  { key: "utilization", label: "Utilization, %" },
  { key: "lltv", label: "LTV Range, %" },
];
const TABLE_MODE_COPY = {
  vaults: { title: "Fluid Vaults", countLabel: "vaults", firstColumn: "Vault", emptyLabel: "vaults" },
  markets: { title: "Fluid Markets", countLabel: "markets", firstColumn: "Market", emptyLabel: "markets" },
  assets: { title: "Fluid Assets", countLabel: "assets", firstColumn: "Asset", emptyLabel: "assets" },
};
const SUPPLY_USD_AREA = {
  key: "supplyUsd",
  color: "#34d399",
  name: "Supply",
  format: "dollar",
  tooltipDecimals: 2,
};
const BORROW_USD_AREA = {
  key: "borrowUsd",
  color: "#06b6d4",
  name: "Borrow",
  format: "dollar",
  tooltipDecimals: 2,
};
const UTILIZATION_AREA = {
  key: "utilizationPct",
  color: "#a78bfa",
  name: "Utilization",
  format: "percent",
};
const SOFR_AREA = {
  key: "sofrRate",
  color: "#71717a",
  name: "SOFR",
  format: "percent",
  noFill: true,
  strokeDasharray: "2 4",
  strokeWidth: 1.5,
};
const PRIMARY_BORROW_APY_AREA = {
  key: "primaryBorrowApy",
  color: "#06b6d4",
  name: "USDC Borrow APY",
  format: "percent",
};
const SECONDARY_BORROW_APY_AREA = {
  key: "secondaryBorrowApy",
  color: "#34d399",
  name: "USDT Borrow APY",
  format: "percent",
};

const finiteNumber = (value, fallback = 0) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const formatCurrency = (value) => {
  const a = finiteNumber(value);
  if (a >= 1e9) return `$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(a / 1e3).toFixed(0)}K`;
  return `$${a.toFixed(0)}`;
};

const formatApy = (value) => `${(finiteNumber(value) * 100).toFixed(2)}%`;
const formatPercent = (value, digits = 2) => `${(finiteNumber(value) * 100).toFixed(digits)}%`;

const formatSignedCurrency = (value) => {
  const amount = finiteNumber(value);
  if (Math.abs(amount) < 1) return "$0";
  return `${amount < 0 ? "-" : "+"}${formatCurrency(Math.abs(amount))}`;
};

const shortAddress = (value) => {
  const text = String(value || "");
  if (text.length <= 12) return text || "-";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
};

const formatLltvRange = (row) => {
  const min = finiteNumber(row?.lltvMin, NaN);
  const max = finiteNumber(row?.lltvMax, NaN);
  if (Number.isFinite(min) && Number.isFinite(max) && max > 0) {
    if (Math.abs(max - min) < 0.000001) return formatPercent(max, 1);
    return `${formatPercent(min, 1)}-${formatPercent(max, 1)}`;
  }
  const lltv = finiteNumber(row?.lltv, NaN);
  return Number.isFinite(lltv) && lltv > 0 ? formatPercent(lltv, 1) : "-";
};

const filterHistoryByWindow = (rows, days) => {
  if (!days || !rows.length) return rows;
  const latestTimestamp = rows.reduce(
    (latest, row) => Math.max(latest, finiteNumber(row.timestamp)),
    0,
  );
  if (latestTimestamp <= 0) return rows;
  const minTimestamp = latestTimestamp - days * 24 * 60 * 60;
  return rows.filter((row) => finiteNumber(row.timestamp) >= minTimestamp);
};

const protocolGroupFromKey = (key) => String(key || "").replace("_MARKET", "").toUpperCase();

const normalizeFluidAssetId = (value) => {
  const id = String(value || "").trim().toLowerCase();
  if (id === "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2") {
    return "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
  }
  return id;
};

const groupAssetItems = (items) => {
  const totals = new Map();
  items.forEach(({ symbol, value }) => {
    const normalized = String(symbol || "UNKNOWN").trim() || "UNKNOWN";
    totals.set(normalized, (totals.get(normalized) || 0) + Math.max(0, finiteNumber(value)));
  });
  return [...totals.entries()].map(([symbol, value]) => ({ symbol, value }));
};

function AssetBreakdownBar({ items = [], title = "", loading = false }) {
  const data = useMemo(() => {
    const sorted = [...items].filter((item) => item.value > 0).sort((a, b) => b.value - a.value);
    const total = sorted.reduce((sum, item) => sum + item.value, 0);
    if (total <= 0) return { segments: [], total: 0 };
    const top = sorted.slice(0, 7);
    const rest = sorted.slice(7);
    if (rest.length) {
      top.push({ symbol: "Other", value: rest.reduce((sum, item) => sum + item.value, 0) });
    }
    return {
      segments: top.map((item) => ({
        ...item,
        pct: item.value / total,
        color: getTokenColor(item.symbol),
      })),
      total,
    };
  }, [items]);

  if (loading && !data.segments.length) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 className="w-5 h-5 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!data.segments.length) {
    return (
      <div className="flex items-center justify-center py-6 text-xs text-gray-600 uppercase tracking-widest">
        No data
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] md:text-xs text-gray-400 uppercase tracking-widest font-bold">{title}</span>
        <span className="text-[10px] md:text-xs text-gray-500 uppercase tracking-widest">{formatCurrency(data.total)}</span>
      </div>
      <div className="w-full h-9 flex rounded-sm overflow-hidden bg-[#111]">
        {data.segments.map((segment) => (
          <div
            key={segment.symbol}
            className="h-full"
            style={{
              width: `${(segment.pct * 100).toFixed(2)}%`,
              backgroundColor: segment.color,
              opacity: 0.65,
              minWidth: segment.pct > 0.005 ? "2px" : "0",
            }}
            title={`${segment.symbol} ${formatCurrency(segment.value)}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1.5 mt-3">
        {data.segments.map((segment) => (
          <div key={segment.symbol} className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-[1px]" style={{ backgroundColor: segment.color }} />
            <span className="text-[10px] md:text-[11px] text-gray-400 uppercase tracking-widest font-mono">{segment.symbol}</span>
            <span className="text-[10px] md:text-[11px] text-white font-bold font-mono tracking-wider">
              {(segment.pct * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const compositionColor = (symbol) => (symbol === "Other" ? "#52525b" : getTokenColor(symbol));

const buildCompositionChartData = (rows, valueKey) => {
  const totals = new Map();
  rows.forEach((row) => {
    const symbol = String(row.symbol || "UNKNOWN").trim() || "UNKNOWN";
    const value = finiteNumber(row[valueKey]);
    if (value <= 0) return;
    totals.set(symbol, (totals.get(symbol) || 0) + value);
  });

  const rankedSymbols = [...totals.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([symbol]) => symbol);
  const topSymbols = rankedSymbols.slice(0, COMPOSITION_MAX_ASSETS);
  const hasOther = rankedSymbols.length > topSymbols.length;
  const symbols = hasOther ? [...topSymbols, "Other"] : topSymbols;
  const topSet = new Set(topSymbols);
  const byTimestamp = new Map();

  rows.forEach((row) => {
    const timestamp = finiteNumber(row.timestamp);
    const value = finiteNumber(row[valueKey]);
    if (timestamp <= 0 || value <= 0) return;
    const rawSymbol = String(row.symbol || "UNKNOWN").trim() || "UNKNOWN";
    const symbol = topSet.has(rawSymbol) ? rawSymbol : hasOther ? "Other" : rawSymbol;
    const point = byTimestamp.get(timestamp) || { timestamp };
    point[symbol] = finiteNumber(point[symbol]) + value;
    byTimestamp.set(timestamp, point);
  });

  const data = [...byTimestamp.values()]
    .sort((a, b) => a.timestamp - b.timestamp)
    .map((point) => {
      symbols.forEach((symbol) => {
        if (point[symbol] === undefined) point[symbol] = 0;
      });
      return point;
    })
    .filter((point) => symbols.some((symbol) => finiteNumber(point[symbol]) > 0));

  return { data, symbols };
};

function CompositionTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const date = new Date(label * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  return (
    <div className="bg-[#0a0a0a] border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50 min-w-[220px]">
      <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">{date}</p>
      {[...payload]
        .filter((entry) => finiteNumber(entry.value) > 0)
        .sort((a, b) => finiteNumber(b.value) - finiteNumber(a.value))
        .slice(0, 10)
        .map((entry) => (
          <div key={entry.dataKey} className="flex items-center justify-between gap-4 mb-1">
            <span className="font-bold truncate max-w-[130px]" style={{ color: entry.color }}>{entry.name}</span>
            <span className="text-white">{formatCurrency(entry.value)}</span>
          </div>
        ))}
    </div>
  );
}

function HistoricalCompositionChart({ data = [], symbols = [], loading = false, emptyLabel = "composition history" }) {
  if (loading && !data.length) {
    return (
      <div className="h-[320px] w-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!data.length || !symbols.length) {
    return (
      <div className="h-[320px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
        No {emptyLabel}
      </div>
    );
  }

  return (
    <div className="w-full">
      <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3">
        {symbols.map((symbol) => (
          <div key={symbol} className="flex items-center gap-2">
            <div className="w-2 h-2" style={{ backgroundColor: compositionColor(symbol) }} />
            <span className="text-[9px] text-gray-500 uppercase tracking-widest">{symbol}</span>
          </div>
        ))}
      </div>
      <div className="h-[300px] w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>
          <AreaChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" vertical={false} />
            <XAxis
              dataKey="timestamp"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(value) => new Date(value * 1000).toLocaleDateString("en-US", { month: "short", year: "2-digit" })}
              stroke="#71717a"
              fontSize={12}
              tickMargin={12}
              minTickGap={60}
            />
            <YAxis
              tickFormatter={(value) => formatCurrency(value)}
              stroke="#71717a"
              fontSize={12}
              width={68}
            />
            <Tooltip content={<CompositionTooltip />} cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }} />
            {symbols.map((symbol) => (
              <Area
                key={symbol}
                type="monotone"
                dataKey={symbol}
                name={symbol}
                stackId="composition"
                fill={compositionColor(symbol)}
                fillOpacity={0.68}
                stroke={compositionColor(symbol)}
                strokeWidth={0}
                isAnimationActive={false}
                connectNulls={true}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const FLOW_COLORS = {
  Inflow: "#34d399",
  Outflow: "#fb7185",
};

const sumFlowBy = (rows, key) => {
  const totals = new Map();
  rows.forEach((row) => {
    const id = row[key] || "UNKNOWN";
    totals.set(id, (totals.get(id) || 0) + finiteNumber(row.valueUsd));
  });
  return totals;
};

const flowDir = (action) => {
  if (action === "Supply Inflow" || action === "Borrow Outflow" || action === "Net Inflow") return "Inflow";
  if (action === "Supply Outflow" || action === "Borrow Inflow" || action === "Net Outflow") return "Outflow";
  return null;
};

const proportionalSlots = (items, totals, top, bottom, height, minH = 18, gap = 38) => {
  const slots = new Map();
  if (!items.length) return slots;
  const total = items.reduce((sum, item) => sum + finiteNumber(totals.get(item)), 0);
  const available = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawHeights = items.map((item) => (total <= 0 ? available / items.length : (available * finiteNumber(totals.get(item))) / total));
  const visibleHeights = rawHeights.map((heightValue) => Math.max(minH, heightValue));
  const visibleTotal = visibleHeights.reduce((sum, heightValue) => sum + heightValue, 0);
  const scale = visibleTotal > available ? available / visibleTotal : 1;
  const heights = visibleHeights.map((heightValue) => Math.max(10, heightValue * scale));
  const used = heights.reduce((sum, heightValue) => sum + heightValue, 0) + gap * (items.length - 1);
  let y = top + Math.max(0, (height - top - bottom - used) / 2);
  items.forEach((item, index) => {
    const h = heights[index];
    slots.set(item, { y, h, center: y + h / 2, total: totals.get(item) || 0 });
    y += h + gap;
  });
  return slots;
};

const sortFlowBy = (totals) => (a, b) => {
  if (a === "Other" && b !== "Other") return 1;
  if (b === "Other" && a !== "Other") return -1;
  return totals.get(b) - totals.get(a);
};

function ProtocolFlowChart({ flows = [], protocolName = "", loading = false }) {
  const [tooltip, setTooltip] = useState(null);
  const model = useMemo(() => {
    if (!flows.length) return null;
    const assetTotals = sumFlowBy(flows, "asset");
    const topAssets = new Set(
      [...assetTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([asset]) => asset),
    );
    const grouped = new Map();
    flows.forEach((flow) => {
      const direction = flowDir(flow.action);
      if (!direction) return;
      const asset = topAssets.has(flow.asset) ? flow.asset : "Other";
      const key = `${asset}|${direction}`;
      const current = grouped.get(key) || { asset, inflowUsd: 0, outflowUsd: 0 };
      const valueUsd = finiteNumber(flow.valueUsd);
      if (direction === "Inflow") current.inflowUsd += valueUsd;
      else current.outflowUsd += valueUsd;
      grouped.set(key, current);
    });

    const rows = [];
    [...grouped.values()].forEach((row) => {
      if (row.inflowUsd > 0) rows.push({ protocol: protocolName, asset: row.asset, direction: "Inflow", valueUsd: row.inflowUsd });
      if (row.outflowUsd > 0) rows.push({ protocol: protocolName, asset: row.asset, direction: "Outflow", valueUsd: row.outflowUsd });
    });

    const inflowRows = rows.filter((row) => row.direction === "Inflow");
    const outflowRows = rows.filter((row) => row.direction === "Outflow");
    const inflowAssetTotals = sumFlowBy(inflowRows, "asset");
    const outflowAssetTotals = sumFlowBy(outflowRows, "asset");
    const protocolFlowTotals = new Map([[protocolName, rows.reduce((sum, row) => sum + row.valueUsd, 0)]]);
    const protocolInflowTotals = new Map([[protocolName, inflowRows.reduce((sum, row) => sum + row.valueUsd, 0)]]);
    const protocolOutflowTotals = new Map([[protocolName, outflowRows.reduce((sum, row) => sum + row.valueUsd, 0)]]);
    const netDelta = (protocolInflowTotals.get(protocolName) || 0) - (protocolOutflowTotals.get(protocolName) || 0);
    const inflowAssets = [...inflowAssetTotals.keys()].sort(sortFlowBy(inflowAssetTotals));
    const outflowAssets = [...outflowAssetTotals.keys()].sort(sortFlowBy(outflowAssetTotals));
    return {
      rows,
      inflowRows,
      outflowRows,
      inflowAssetTotals,
      outflowAssetTotals,
      protocolFlowTotals,
      protocolInflowTotals,
      protocolOutflowTotals,
      inflowAssets,
      outflowAssets,
      netDelta,
    };
  }, [flows, protocolName]);

  if (loading && !flows.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!model || !model.rows.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-600 uppercase tracking-widest">
        No flow data
      </div>
    );
  }

  const width = 1120;
  const height = 390;
  const top = 20;
  const bottom = 20;
  const nodeWidth = 12;
  const xInflow = 165;
  const xProtocol = 555;
  const xOutflow = 930;
  const headerY = 14;

  const inflowLayout = proportionalSlots(model.inflowAssets, model.inflowAssetTotals, top + 22, bottom + 8, height, 10, 9);
  const protocolLayout = proportionalSlots([protocolName], model.protocolFlowTotals, top + 64, bottom + 38, height, 40, 0);
  const outflowLayout = proportionalSlots(model.outflowAssets, model.outflowAssetTotals, top + 22, bottom + 8, height, 10, 9);

  const ribbonPath = (x1, source, x2, target) => {
    const mid = (x1 + x2) / 2;
    return [
      `M ${x1.toFixed(2)} ${source.y0.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${source.y0.toFixed(2)}, ${mid.toFixed(2)} ${target.y0.toFixed(2)}, ${x2.toFixed(2)} ${target.y0.toFixed(2)}`,
      `L ${x2.toFixed(2)} ${target.y1.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${target.y1.toFixed(2)}, ${mid.toFixed(2)} ${source.y1.toFixed(2)}, ${x1.toFixed(2)} ${source.y1.toFixed(2)}`,
      "Z",
    ].join(" ");
  };
  const linkSegment = (layout, totals, offsets, key, value) => {
    const slot = layout.get(key);
    if (!slot) return null;
    const total = Math.max(1, finiteNumber(totals.get(key)));
    const dy = (slot.h * finiteNumber(value)) / total;
    const offset = offsets.get(key) || 0;
    offsets.set(key, offset + dy);
    return { y0: slot.y + offset, y1: slot.y + offset + dy };
  };

  const inflowOffsets = new Map();
  const outflowOffsets = new Map();
  const protocolInflowOffsets = new Map();
  const protocolOutflowOffsets = new Map();
  const showTooltip = (event, link) => {
    const bounds = event.currentTarget.ownerSVGElement.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const adjustedX = x > bounds.width - 160 ? x - 170 : x + 15;
    setTooltip({ x: adjustedX, y: y + 15, link });
  };

  const inflowLinks = model.inflowRows
    .sort((a, b) => b.valueUsd - a.valueUsd)
    .map((row) => {
      const source = linkSegment(inflowLayout, model.inflowAssetTotals, inflowOffsets, row.asset, row.valueUsd);
      const target = linkSegment(protocolLayout, model.protocolInflowTotals, protocolInflowOffsets, protocolName, row.valueUsd);
      if (!source || !target) return null;
      return {
        d: ribbonPath(xInflow + nodeWidth, source, xProtocol, target),
        color: FLOW_COLORS.Inflow,
        sourceName: row.asset,
        targetName: protocolName,
        valueUsd: row.valueUsd,
      };
    })
    .filter(Boolean);

  const outflowLinks = model.outflowRows
    .sort((a, b) => b.valueUsd - a.valueUsd)
    .map((row) => {
      const source = linkSegment(protocolLayout, model.protocolOutflowTotals, protocolOutflowOffsets, protocolName, row.valueUsd);
      const target = linkSegment(outflowLayout, model.outflowAssetTotals, outflowOffsets, row.asset, row.valueUsd);
      if (!source || !target) return null;
      return {
        d: ribbonPath(xProtocol + nodeWidth, source, xOutflow, target),
        color: FLOW_COLORS.Outflow,
        sourceName: protocolName,
        targetName: row.asset,
        valueUsd: row.valueUsd,
      };
    })
    .filter(Boolean);

  const renderNodes = (items, totals, slots, x, anchor, fallbackColor) => items.map((item) => {
    const slot = slots.get(item);
    if (!slot) return null;
    const color = item === protocolName ? "#998EFF" : fallbackColor;
    const labelX = anchor === "end" ? x - 8 : anchor === "middle" ? x + nodeWidth / 2 : x + nodeWidth + 8;
    const isProtocol = anchor === "middle";
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={color} rx="2" />
        {isProtocol ? (
          <text x={labelX} y={slot.y - 8} textAnchor="middle" fill="#e5e7eb" fontSize="12">
            {`${item}  ${formatSignedCurrency(model.netDelta)}`}
          </text>
        ) : (
          <text x={labelX} y={slot.y + slot.h / 2 + 4} textAnchor={anchor} fill="#e5e7eb" fontSize="12">
            {`${item} ${formatCurrency(totals.get(item))}`}
          </text>
        )}
      </g>
    );
  });

  return (
    <div className="relative w-full h-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full" role="img" aria-label="Protocol flow alluvial chart">
        <g>
          {[...inflowLinks, ...outflowLinks].map((link, index) => (
            <path
              key={`${link.sourceName}-${link.targetName}-${index}`}
              d={link.d}
              fill={link.color}
              fillOpacity="0.32"
              stroke="none"
              onMouseMove={(event) => showTooltip(event, link)}
              onMouseLeave={() => setTooltip(null)}
              className="transition-opacity hover:opacity-80 cursor-default"
            />
          ))}
          <text x={xInflow + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET INFLOWS</text>
          <text x={xProtocol + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">PROTOCOL</text>
          <text x={xOutflow + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET OUTFLOWS</text>
          {renderNodes(model.inflowAssets, model.inflowAssetTotals, inflowLayout, xInflow, "end", FLOW_COLORS.Inflow)}
          {renderNodes([protocolName], model.protocolFlowTotals, protocolLayout, xProtocol, "middle")}
          {renderNodes(model.outflowAssets, model.outflowAssetTotals, outflowLayout, xOutflow, "start", FLOW_COLORS.Outflow)}
        </g>
      </svg>
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none rounded-sm border border-zinc-800 bg-[#0a0a0a] px-3 py-2 text-xs font-mono shadow-2xl"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2 border-b border-white/10 pb-1.5 mb-0.5">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: tooltip.link.color }} />
              <span className="font-bold text-white uppercase tracking-wider">{tooltip.link.sourceName} {"->"} {tooltip.link.targetName}</span>
            </div>
            <div className="flex justify-between gap-6">
              <span className="text-gray-500 uppercase tracking-widest text-[10px]">Net Flow</span>
              <span className="text-white">{formatCurrency(tooltip.link.valueUsd)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function FluidProtocolPage() {
  const navigate = useNavigate();
  const [tableMode, setTableMode] = useState("vaults");
  const [currentPage, setCurrentPage] = useState(1);
  const [sortKey, setSortKey] = useState("supplyUsd");
  const [sortDir, setSortDir] = useState("desc");
  const [flowWindowDays, setFlowWindowDays] = useState(7);
  const [balanceWindowDays, setBalanceWindowDays] = useState(null);
  const [rateWindowDays, setRateWindowDays] = useState(365);
  const [utilizationWindowDays, setUtilizationWindowDays] = useState(365);
  const [collateralCompositionWindowDays, setCollateralCompositionWindowDays] = useState(365);
  const [debtCompositionWindowDays, setDebtCompositionWindowDays] = useState(365);
  const activeFlowWindow = FLOW_WINDOWS.find((window) => window.days === flowWindowDays) || FLOW_WINDOWS[1];

  const { data: assetsGql, isLoading: assetsLoading } = useSWR(
    queryKeys.apiProtocolMarkets(API_GRAPHQL_URL, PROTOCOL_KEY),
    ([, , variables]) =>
      apiGraphQL("ProtocolMarketsByProtocol", {
        query: PROTOCOL_MARKETS_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: vaultsGql, isLoading: vaultsLoading } = useSWR(
    queryKeys.apiFluidProductSnapshots(API_GRAPHQL_URL, "FTOKEN", 2000),
    ([, , variables]) =>
      apiGraphQL("FluidProductSnapshots", {
        query: FLUID_PRODUCT_SNAPSHOTS_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: marketsGql, isLoading: marketsLoading } = useSWR(
    queryKeys.apiFluidProductSnapshots(API_GRAPHQL_URL, "VAULT", 2000),
    ([, , variables]) =>
      apiGraphQL("FluidProductSnapshots", {
        query: FLUID_PRODUCT_SNAPSHOTS_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: apyGql, isLoading: apyLoading } = useSWR(
    queryKeys.apiProtocolApyHistory(API_GRAPHQL_URL, PROTOCOL_KEY, APY_RESOLUTION, APY_LIMIT),
    ([, , variables]) =>
      apiGraphQL("ProtocolApyHistory", {
        query: PROTOCOL_APY_HISTORY_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: assetApyGql, isLoading: assetApyLoading } = useSWR(
    queryKeys.apiProtocolAssetApyHistory(API_GRAPHQL_URL, PROTOCOL_KEY, ["USDC", "USDT"], APY_RESOLUTION, APY_LIMIT * 2),
    ([, , variables]) =>
      apiGraphQL("ProtocolAssetApyHistory", {
        query: PROTOCOL_ASSET_APY_HISTORY_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: flowsGql, isLoading: flowsLoading } = useSWR(
    queryKeys.apiLendingPage(API_GRAPHQL_URL, "USD", flowWindowDays),
    ([, , variables]) =>
      apiGraphQL("LendingDataHub", {
        query: LENDING_DATA_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const stats = useMemo(() => {
    const s = assetsGql?.protocolMarketsPage?.stats;
    return {
      totalSupply: finiteNumber(s?.totalSupplyUsd),
      totalBorrow: finiteNumber(s?.totalBorrowUsd),
      avgSupplyApy: finiteNumber(s?.averageSupplyApy),
      avgBorrowApy: finiteNumber(s?.averageBorrowApy),
      avgUtil: finiteNumber(s?.averageUtilization),
      count: finiteNumber(s?.marketCount),
    };
  }, [assetsGql]);

  const ltvRangeByDebtToken = useMemo(() => {
    const ranges = new Map();
    const rows = marketsGql?.fluidProductSnapshots || [];
    rows.forEach((row) => {
      if (String(row.productType || "").toUpperCase() !== "VAULT") return;
      const debtToken = String(row.debtToken || "").trim().toLowerCase();
      const ltv = finiteNumber(row.ltv, NaN);
      if (!debtToken || !Number.isFinite(ltv) || ltv <= 0) return;
      const current = ranges.get(debtToken);
      ranges.set(debtToken, {
        min: current ? Math.min(current.min, ltv) : ltv,
        max: current ? Math.max(current.max, ltv) : ltv,
      });
    });
    return ranges;
  }, [marketsGql]);

  const assetRows = useMemo(() => {
    const rows = assetsGql?.protocolMarketsPage?.rows || [];
    return rows.map((row) => {
      const entityId = normalizeFluidAssetId(row.entityId);
      const ltvRange = ltvRangeByDebtToken.get(entityId);
      return {
        rowType: "asset",
        entityId: row.entityId,
        symbol: row.symbol,
        marketLabel: row.symbol,
        collateralSymbol: null,
        protocol: row.protocol || PROTOCOL_KEY,
        detailPath: `/data/fluid/assets/${row.entityId}`,
        supplyUsd: finiteNumber(row.supplyUsd),
        borrowUsd: finiteNumber(row.borrowUsd),
        netWorth: Math.max(0, finiteNumber(row.supplyUsd) - finiteNumber(row.borrowUsd)),
        supplyApy: finiteNumber(row.supplyApy),
        borrowApy: finiteNumber(row.borrowApy),
        utilization: finiteNumber(row.utilization),
        lltv: ltvRange?.max ?? finiteNumber(row.lltv),
        lltvMin: ltvRange?.min ?? row.lltvMin,
        lltvMax: ltvRange?.max ?? row.lltvMax,
        loanIcon: getTokenIcon(row.symbol),
        collateralIcon: null,
      };
    });
  }, [assetsGql, ltvRangeByDebtToken]);

  const { data: compositionGql, isLoading: compositionLoading } = useSWR(
    queryKeys.apiFluidVaultCompositionHistory(API_GRAPHQL_URL, APY_RESOLUTION, 50000),
    ([, , variables]) =>
      apiGraphQL("FluidVaultCompositionHistory", {
        query: FLUID_VAULT_COMPOSITION_HISTORY_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const assetMetricsByEntityId = useMemo(() => {
    const map = new Map();
    assetRows.forEach((row) => {
      const entityId = normalizeFluidAssetId(row.entityId);
      if (entityId) map.set(entityId, row);
    });
    return map;
  }, [assetRows]);

  const vaultRows = useMemo(() => {
    const rows = vaultsGql?.fluidProductSnapshots || [];
    const seen = new Set();
    return rows
      .filter((row) => String(row.productType || "").toUpperCase() === "FTOKEN")
      .filter((row) => {
        const id = String(row.productId || "").trim().toLowerCase();
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return finiteNumber(row.supplyUsd) + finiteNumber(row.liquidityUsd) > 0;
      })
      .map((row) => {
        const underlyingId = normalizeFluidAssetId(row.underlying);
        const asset = assetMetricsByEntityId.get(underlyingId);
        const rawSymbol = String(row.symbol || "").trim();
        const displaySymbol = asset?.symbol || (rawSymbol.startsWith("f") && rawSymbol.length > 1 ? rawSymbol.slice(1) : rawSymbol) || shortAddress(row.productId);
        const supplyUsd = finiteNumber(row.supplyUsd);
        const borrowUsd = finiteNumber(asset?.borrowUsd);
        const withdrawableUsd = finiteNumber(asset?.netWorth, finiteNumber(row.liquidityUsd, supplyUsd));
        return {
          rowType: "vault",
          entityId: row.productId,
          symbol: displaySymbol,
          marketLabel: displaySymbol,
          collateralSymbol: null,
          protocol: PROTOCOL_KEY,
          detailPath: underlyingId ? `/data/fluid/assets/${underlyingId}` : `/data/fluid/assets/${row.productId}`,
          supplyUsd,
          borrowUsd,
          withdrawableUsd,
          netWorth: supplyUsd,
          supplyApy: finiteNumber(asset?.supplyApy, finiteNumber(row.supplyApy)),
          borrowApy: finiteNumber(asset?.borrowApy),
          utilization: finiteNumber(asset?.utilization),
          lltv: asset?.lltv ?? null,
          lltvMin: asset?.lltvMin ?? null,
          lltvMax: asset?.lltvMax ?? null,
          pricingStatus: String(row.pricingStatus || ""),
          snapshotStatus: String(row.snapshotStatus || ""),
          loanIcon: getTokenIcon(displaySymbol),
          collateralIcon: null,
        };
      });
  }, [assetMetricsByEntityId, vaultsGql]);

  const marketRows = useMemo(() => {
    const rows = marketsGql?.fluidProductSnapshots || [];
    const seen = new Set();
    return rows
      .filter((row) => String(row.productType || "").toUpperCase() === "VAULT")
      .filter((row) => {
        const id = String(row.productId || "").trim().toLowerCase();
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return (
          finiteNumber(row.liquidityUsd)
          + finiteNumber(row.supplyUsd)
          + finiteNumber(row.borrowUsd)
          + finiteNumber(row.collateralUsd)
        ) > 0;
      })
      .map((row) => {
        const symbol = String(row.symbol || "").trim() || shortAddress(row.productId);
        const [collateralPart, debtPart] = symbol.includes("/")
          ? symbol.split("/").map((part) => part.trim())
          : [symbol, ""];
        const collateralSymbol = collateralPart || symbol;
        const debtSymbol = debtPart || "";
        const liquidityUsd = finiteNumber(row.liquidityUsd);
        const supplyUsd = finiteNumber(row.supplyUsd);
        const borrowUsd = finiteNumber(row.borrowUsd);
        const ltv = finiteNumber(row.ltv);
        return {
          rowType: "market",
          entityId: row.productId,
          symbol,
          marketLabel: symbol,
          assetSymbol: debtSymbol || collateralSymbol,
          collateralSymbol: debtSymbol ? collateralSymbol : null,
          protocol: PROTOCOL_KEY,
          detailPath: `/data/fluid/markets/${row.productId}`,
          supplyUsd,
          borrowUsd,
          collateralUsd: finiteNumber(row.collateralUsd),
          netWorth: liquidityUsd > 0 ? liquidityUsd : Math.max(0, supplyUsd - borrowUsd),
          supplyApy: finiteNumber(row.supplyApy),
          borrowApy: finiteNumber(row.borrowApy),
          utilization: finiteNumber(row.utilization),
          lltv: ltv,
          lltvMin: ltv > 0 ? ltv : null,
          lltvMax: ltv > 0 ? ltv : null,
          positionCount: finiteNumber(row.positionCount),
          pricingStatus: String(row.pricingStatus || ""),
          snapshotStatus: String(row.snapshotStatus || ""),
          loanIcon: getTokenIcon(debtSymbol || collateralSymbol),
          collateralIcon: debtSymbol ? getTokenIcon(collateralSymbol) : null,
        };
      });
  }, [marketsGql]);

  const balanceChartData = useMemo(() => {
    const rows = apyGql?.protocolApyHistory || [];
    return [...rows]
      .filter((point) => finiteNumber(point.timestamp) > 0)
      .sort((a, b) => finiteNumber(a.timestamp) - finiteNumber(b.timestamp));
  }, [apyGql]);
  const visibleBalanceChartData = useMemo(
    () => filterHistoryByWindow(balanceChartData, balanceWindowDays),
    [balanceChartData, balanceWindowDays],
  );

  const utilizationChartData = useMemo(() => {
    return balanceChartData
      .map((point) => {
        const supplyUsd = finiteNumber(point.supplyUsd);
        const borrowUsd = finiteNumber(point.borrowUsd);
        return {
          timestamp: finiteNumber(point.timestamp),
          utilizationPct: supplyUsd > 0 ? (borrowUsd / supplyUsd) * 100 : null,
        };
      })
      .filter((point) => Number.isFinite(Number(point.utilizationPct)));
  }, [balanceChartData]);
  const visibleUtilizationChartData = useMemo(
    () => filterHistoryByWindow(utilizationChartData, utilizationWindowDays),
    [utilizationChartData, utilizationWindowDays],
  );

  const stablecoinRateChartData = useMemo(() => {
    const rows = assetApyGql?.protocolAssetApyHistory || [];
    const byTimestamp = new Map();
    rows.forEach((row) => {
      const timestamp = finiteNumber(row.timestamp);
      if (timestamp <= 0) return;
      const symbol = String(row.symbol || "").toUpperCase();
      const point = byTimestamp.get(timestamp) || { timestamp };
      if (symbol === "USDC") point.primaryBorrowApy = finiteNumber(row.borrowApy) * 100;
      if (symbol === "USDT") point.secondaryBorrowApy = finiteNumber(row.borrowApy) * 100;
      if (row.sofrRate !== null && row.sofrRate !== undefined) {
        point.sofrRate = finiteNumber(row.sofrRate) * 100;
      }
      byTimestamp.set(timestamp, point);
    });
    return [...byTimestamp.values()]
      .filter((point) => [point.primaryBorrowApy, point.secondaryBorrowApy].some((value) => Number.isFinite(Number(value))))
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [assetApyGql]);
  const visibleStablecoinRateChartData = useMemo(
    () => filterHistoryByWindow(stablecoinRateChartData, rateWindowDays),
    [rateWindowDays, stablecoinRateChartData],
  );

  const marketCompositionHistoryRows = useMemo(() => {
    const rows = compositionGql?.fluidVaultCompositionHistory || [];
    return [...rows]
      .filter((point) => finiteNumber(point.timestamp) > 0)
      .map((point) => {
        const symbol = String(point.symbol || "UNKNOWN").trim() || "UNKNOWN";
        return {
          timestamp: finiteNumber(point.timestamp),
          symbol,
          collateralUsd: finiteNumber(point.collateralUsd),
          debtUsd: finiteNumber(point.debtUsd),
        };
      })
      .sort((a, b) => finiteNumber(a.timestamp) - finiteNumber(b.timestamp));
  }, [compositionGql]);

  const collateralCompositionChart = useMemo(
    () => buildCompositionChartData(
      filterHistoryByWindow(
        marketCompositionHistoryRows.map((row) => ({
          timestamp: row.timestamp,
          symbol: row.symbol,
          collateralUsd: row.collateralUsd,
        })),
        collateralCompositionWindowDays,
      ),
      "collateralUsd",
    ),
    [marketCompositionHistoryRows, collateralCompositionWindowDays],
  );

  const debtCompositionChart = useMemo(
    () => buildCompositionChartData(
      filterHistoryByWindow(
        marketCompositionHistoryRows.map((row) => ({
          timestamp: row.timestamp,
          symbol: row.symbol,
          debtUsd: row.debtUsd,
        })),
        debtCompositionWindowDays,
      ),
      "debtUsd",
    ),
    [marketCompositionHistoryRows, debtCompositionWindowDays],
  );

  const protocolFlows = useMemo(() => {
    const rows = flowsGql?.lendingDataPage?.alluvialFlows || [];
    return rows.filter((row) => protocolGroupFromKey(row.protocol) === "FLUID");
  }, [flowsGql]);

  const flowTotals = useMemo(() => {
    let netInflow = 0;
    let netOutflow = 0;
    protocolFlows.forEach((row) => {
      const valueUsd = finiteNumber(row.valueUsd);
      const action = String(row.action || "");
      if (action === "Supply Inflow" || action === "Net Inflow") netInflow += valueUsd;
      if (action === "Supply Outflow" || action === "Net Outflow") netOutflow += valueUsd;
    });
    return { netInflow, netOutflow, netFlow: netInflow - netOutflow };
  }, [protocolFlows]);

  const supplyItems = useMemo(
    () => groupAssetItems(assetRows.map((asset) => ({ symbol: asset.symbol, value: asset.supplyUsd }))),
    [assetRows],
  );
  const debtItems = useMemo(
    () => groupAssetItems(assetRows.map((asset) => ({ symbol: asset.symbol, value: asset.borrowUsd }))),
    [assetRows],
  );

  const activeTableMode = TABLE_MODES.some((mode) => mode.key === tableMode) ? tableMode : "vaults";
  const tableModeCopy = TABLE_MODE_COPY[activeTableMode] || TABLE_MODE_COPY.vaults;
  const tableColumns = activeTableMode === "vaults"
    ? VAULT_COLUMNS
    : activeTableMode === "markets"
      ? MARKET_COLUMNS
      : ASSET_COLUMNS;
  const tableRows = activeTableMode === "vaults"
    ? vaultRows
    : activeTableMode === "markets"
      ? marketRows
      : assetRows;
  const tableLoading = activeTableMode === "vaults"
    ? vaultsLoading
    : activeTableMode === "markets"
      ? marketsLoading
      : assetsLoading;
  const tableGridClass = "grid-cols-9";

  const handleSort = useCallback((key) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((dir) => (dir === "desc" ? "asc" : "desc"));
        return key;
      }
      setSortDir("desc");
      return key;
    });
    setCurrentPage(1);
  }, []);

  const handleTableModeChange = useCallback((mode) => {
    setTableMode(mode);
    setSortKey("supplyUsd");
    setSortDir("desc");
    setCurrentPage(1);
  }, []);

  const sortedRows = useMemo(() => {
    const mul = sortDir === "desc" ? -1 : 1;
    return tableRows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => {
        const aValue = a.row[sortKey];
        const bValue = b.row[sortKey];
        if (typeof aValue === "string" || typeof bValue === "string") {
          const compared = String(aValue || "").localeCompare(String(bValue || ""));
          return compared === 0 ? a.index - b.index : compared * mul;
        }
        const aNumber = finiteNumber(aValue);
        const bNumber = finiteNumber(bValue);
        if (aNumber === bNumber) return a.index - b.index;
        return (aNumber - bNumber) * mul;
      })
      .map(({ row }) => row);
  }, [sortDir, sortKey, tableRows]);

  const totalPages = Math.ceil(sortedRows.length / PAGE_SIZE) || 1;
  const safePage = Math.min(currentPage, totalPages);
  const pagedRows = useMemo(
    () => sortedRows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [safePage, sortedRows],
  );

  const renderSortHeader = (key, label, className = "text-center justify-center") => {
    const active = sortKey === key;
    const Icon = active && sortDir === "asc" ? ChevronUp : ChevronDown;
    return (
      <button
        type="button"
        onClick={() => handleSort(key)}
        className={`group flex w-full items-center gap-1.5 transition-colors hover:text-gray-300 ${className} ${active ? "text-cyan-400" : "text-gray-500"}`}
      >
        <span>{label}</span>
        <Icon size={13} className={`shrink-0 transition-opacity ${active ? "opacity-100" : "opacity-35 group-hover:opacity-70"}`} />
      </button>
    );
  };

  const renderHistoryWindowControls = (activeDays, setActiveDays) => (
    <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
      {HISTORY_WINDOWS.map((window) => (
        <button
          key={window.label}
          type="button"
          onClick={() => setActiveDays(window.days)}
          className={`px-2.5 py-1 text-[10px] uppercase tracking-widest rounded-sm transition-colors ${activeDays === window.days
            ? "bg-white/10 text-white"
            : "text-gray-500 hover:text-gray-300"
            }`}
        >
          {window.label}
        </button>
      ))}
    </div>
  );

  const renderTableCell = (row, column) => {
    if (column.key === "netWorth") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.netWorth)}</div>;
    }
    if (column.key === "withdrawableUsd") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.withdrawableUsd)}</div>;
    }
    if (column.key === "supplyUsd") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.supplyUsd)}</div>;
    }
    if (column.key === "borrowUsd") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.borrowUsd)}</div>;
    }
    if (column.key === "supplyApy") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{formatApy(row.supplyApy)}</div>;
    }
    if (column.key === "borrowApy") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-cyan-500 tracking-widest">{formatApy(row.borrowApy)}</div>;
    }
    if (column.key === "utilization") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{formatPercent(row.utilization)}</div>;
    }
    if (column.key === "lltv") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{formatLltvRange(row)}</div>;
    }
    return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-500 tracking-widest">-</div>;
  };

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        <div className="flex items-center gap-3 mb-6 transition-all duration-500">
          <span className="font-mono text-[#333] text-[12px]">|-</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span className="text-[#999]">/</span>
            <span className="flex items-center gap-2 text-white">
              <img src={getProtocolIcon(PROTOCOL_KEY)} alt={DISPLAY_NAME} className="w-4 h-4 rounded-full" />
              {DISPLAY_NAME}
            </span>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={(props) => (
                <a
                  href="https://fluid.instadapp.io"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-white transition-colors"
                  onClick={(event) => event.stopPropagation()}
                >
                  <ExternalLink {...props} />
                </a>
              )}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col gap-6 mt-auto">
                  <div className="flex items-center gap-3">
                    <img src={getProtocolIcon(PROTOCOL_KEY)} alt={DISPLAY_NAME} className="w-8 h-8 rounded-full" />
                    <span className="text-base md:text-4xl font-light text-white font-mono tracking-tighter uppercase">{DISPLAY_NAME}</span>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="TYPE" value="Lending Protocol" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="ASSETS"
              Icon={PieChartIcon}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SUPPLIED" value={marketsLoading ? "..." : formatCurrency(stats.totalSupply)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROWED" value={marketsLoading ? "..." : formatCurrency(stats.totalBorrow)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="LIQUIDITY" value={marketsLoading ? "..." : formatCurrency(Math.max(0, stats.totalSupply - stats.totalBorrow))} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SUPPLY APR" value={marketsLoading ? "..." : formatApy(stats.avgSupplyApy)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROW APR" value={marketsLoading ? "..." : formatApy(stats.avgBorrowApy)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="UTILIZATION" value={marketsLoading ? "..." : formatPercent(stats.avgUtil)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label={`FLOWS ${activeFlowWindow.label}`}
              Icon={ArrowUpRight}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">NET INFLOW</div>
                      <div className="text-base md:text-xl font-light text-emerald-400 font-mono tracking-tighter">
                        {flowTotals.netInflow > 0 ? formatSignedCurrency(flowTotals.netInflow) : "$0"}
                      </div>
                    </div>
                    <div className="border-l border-white/10 pl-4">
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">NET OUTFLOW</div>
                      <div className="text-base md:text-xl font-light text-rose-400 font-mono tracking-tighter">
                        {flowTotals.netOutflow > 0 ? formatSignedCurrency(-flowTotals.netOutflow) : "$0"}
                      </div>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">NET FLOW</div>
                    <div className={`text-base md:text-xl font-light font-mono tracking-tighter ${flowTotals.netFlow >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {formatSignedCurrency(flowTotals.netFlow)}
                    </div>
                  </div>
                </div>
              }
            />
          </div>
        </div>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Fluid Supply & Borrow</h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(balanceWindowDays, setBalanceWindowDays)}
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {apyLoading && balanceChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleBalanceChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">No balance history available</div>
              ) : (
                <RLDPerformanceChart data={visibleBalanceChartData} areas={[SUPPLY_USD_AREA, BORROW_USD_AREA]} resolution={APY_RESOLUTION} />
              )}
            </div>
          </div>

          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Composition</h2>
            </div>
            <div className="flex flex-col flex-1 gap-8 justify-between">
              <AssetBreakdownBar items={supplyItems} title="SUPPLY MIX" loading={marketsLoading} />
              <AssetBreakdownBar items={debtItems} title="DEBT MIX" loading={marketsLoading} />
            </div>
          </div>
        </section>

        <section className="mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                Fluid Flows ({activeFlowWindow.label})
              </h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
                  {FLOW_WINDOWS.map((window) => (
                    <button
                      key={window.label}
                      type="button"
                      onClick={() => setFlowWindowDays(window.days)}
                      className={`px-2.5 py-1 text-[10px] uppercase tracking-widest rounded-sm transition-colors ${flowWindowDays === window.days
                        ? "bg-white/10 text-white"
                        : "text-gray-500 hover:text-gray-300"
                        }`}
                    >
                      {window.label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3">
                  {Object.entries(FLOW_COLORS).map(([label, color]) => (
                    <div key={label} className="flex items-center gap-2">
                      <div className="w-2 h-2" style={{ backgroundColor: color }} />
                      <span className="text-[9px] text-gray-500 uppercase tracking-widest">{`NET_${label}`}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="h-[390px] w-full relative mt-auto">
              <ProtocolFlowChart
                flows={protocolFlows}
                protocolName={DISPLAY_NAME}
                loading={flowsLoading}
              />
            </div>
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">USDC/USDT Interest Rates</h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(rateWindowDays, setRateWindowDays)}
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {assetApyLoading && stablecoinRateChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleStablecoinRateChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">No rate history available</div>
              ) : (
                <RLDPerformanceChart
                  data={visibleStablecoinRateChartData}
                  areas={[PRIMARY_BORROW_APY_AREA, SECONDARY_BORROW_APY_AREA, SOFR_AREA]}
                  resolution={APY_RESOLUTION}
                />
              )}
            </div>
          </div>

          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Historical Utilization</h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(utilizationWindowDays, setUtilizationWindowDays)}
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {apyLoading && utilizationChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleUtilizationChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">No utilization history available</div>
              ) : (
                <RLDPerformanceChart data={visibleUtilizationChartData} areas={[UTILIZATION_AREA]} resolution={APY_RESOLUTION} yAxisDomain={[0, "auto"]} />
              )}
            </div>
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Historical Collateral Composition</h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(collateralCompositionWindowDays, setCollateralCompositionWindowDays)}
              </div>
            </div>
            <HistoricalCompositionChart
              data={collateralCompositionChart.data}
              symbols={collateralCompositionChart.symbols}
              loading={compositionLoading}
              emptyLabel="collateral composition history"
            />
          </div>

          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Historical Debt Composition</h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(debtCompositionWindowDays, setDebtCompositionWindowDays)}
              </div>
            </div>
            <HistoricalCompositionChart
              data={debtCompositionChart.data}
              symbols={debtCompositionChart.symbols}
              loading={compositionLoading}
              emptyLabel="debt composition history"
            />
          </div>
        </section>

        <section className="mt-8 border border-white/10 bg-[#080808]">
          <div className="flex flex-col md:flex-row md:items-center justify-between p-4 md:px-6 border-b border-white/10 gap-4">
            <div className="flex items-center gap-3">
              <img src={getProtocolIcon(PROTOCOL_KEY)} alt={DISPLAY_NAME} className="w-7 h-7 rounded-full" />
              <div>
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  {tableModeCopy.title}
                </h2>
                <div className="text-[10px] text-gray-600 uppercase tracking-widest">
                  {sortedRows.length} {tableModeCopy.countLabel}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4 flex-wrap justify-end">
              <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
                {TABLE_MODES.map((mode) => (
                  <button
                    key={mode.key}
                    type="button"
                    onClick={() => handleTableModeChange(mode.key)}
                    className={`px-2.5 py-1 text-[10px] uppercase tracking-widest rounded-sm transition-colors ${activeTableMode === mode.key
                      ? "bg-white/10 text-white"
                      : "text-gray-500 hover:text-gray-300"
                      }`}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
              <div className="text-[10px] text-gray-500 uppercase tracking-widest">
                Data provided by <span className="text-white">RLD Protocol</span>
              </div>
            </div>
          </div>

          <div className="w-full overflow-x-auto">
            <div className="min-w-[1120px] flex flex-col">
              <div className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]`}>
                <div className="col-span-2">{renderSortHeader("symbol", tableModeCopy.firstColumn, "justify-start text-left")}</div>
                {tableColumns.map((column) => (
                  <div key={column.key}>{renderSortHeader(column.key, column.label)}</div>
                ))}
              </div>

              <div className="flex flex-col divide-y divide-white/5 relative min-h-[200px]">
                {tableLoading && tableRows.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center mt-12">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : pagedRows.length === 0 ? (
                  <div className="flex items-center justify-center py-12 text-xs text-gray-600 uppercase tracking-widest">
                    No {tableModeCopy.emptyLabel} available
                  </div>
                ) : (
                  pagedRows.map((row) => (
                    <div
                      key={row.entityId}
                      onClick={() => {
                        navigate(row.detailPath || marketRouteFor(row.protocol || PROTOCOL_KEY, row.entityId));
                      }}
                      className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-4 items-center transition-colors hover:bg-white/[0.02] group cursor-pointer`}
                    >
                      <div className="col-span-2 flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm group-hover:border-white/20 transition-colors">
                          <img
                            src={row.collateralIcon || row.loanIcon}
                            alt={row.collateralSymbol || row.symbol}
                            className="w-full h-full object-contain rounded-full"
                            loading="lazy"
                            onError={(event) => {
                              event.currentTarget.src = `https://ui-avatars.com/api/?name=${row.collateralSymbol || row.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                            }}
                          />
                        </div>
                        {row.collateralSymbol && (
                          <div className="w-7 h-7 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm group-hover:border-white/20 transition-colors -ml-6 mt-5">
                            <img
                              src={row.loanIcon}
                              alt={row.assetSymbol || row.symbol}
                              className="w-full h-full object-contain rounded-full"
                              loading="lazy"
                              onError={(event) => {
                                event.currentTarget.src = `https://ui-avatars.com/api/?name=${row.assetSymbol || row.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                              }}
                            />
                          </div>
                        )}
                        <div className="flex min-h-10 min-w-0 flex-col justify-center text-[10px] md:text-[13px] text-white font-bold leading-none">
                          <span className="truncate" title={row.marketLabel}>{row.marketLabel}</span>
                        </div>
                      </div>
                      {tableColumns.map((column) => (
                        <div key={`${row.entityId}-${column.key}`}>
                          {renderTableCell(row, column)}
                        </div>
                      ))}
                    </div>
                  ))
                )}
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-6 px-4 md:px-6 py-4 border-t border-white/10 bg-[#080808]">
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    Page {safePage} of {totalPages}
                  </span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setCurrentPage(safePage - 1)}
                      disabled={safePage === 1}
                      className="px-3 py-1 bg-[#111] border border-white/10 text-xs text-gray-300 uppercase tracking-widest hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Prev
                    </button>
                    <button
                      type="button"
                      onClick={() => setCurrentPage(safePage + 1)}
                      disabled={safePage === totalPages}
                      className="px-3 py-1 bg-[#111] border border-white/10 text-xs text-gray-300 uppercase tracking-widest hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
