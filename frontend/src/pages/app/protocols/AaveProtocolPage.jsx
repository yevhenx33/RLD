import React, { useMemo, useState, useCallback, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import useSWR from "swr";
import {
  Activity,
  PieChart as PieChartIcon,
  ArrowUpRight,
  Loader2,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
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
  PROTOCOL_MARKETS_QUERY,
  COMPOUND_V3_PROTOCOL_PAGE_QUERY,
  METAMORPHO_VAULTS_QUERY,
  PROTOCOL_APY_HISTORY_QUERY,
  PROTOCOL_ASSET_APY_HISTORY_QUERY,
  LENDING_DATA_QUERY,
  MORPHO_CURATOR_FLOWS_QUERY,
  EULER_CHANNEL_FLOWS_QUERY,
  MORPHO_CURATOR_ALLOCATION_HISTORY_QUERY,
} from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import {
  apiProtocolForSlug,
  marketRouteFor,
} from "../../../lib/protocolConfig";
import {
  getTokenIcon,
  getProtocolIcon,
  getProtocolDisplayName,
  getCuratorIcon,
  getTokenColor,
} from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const APY_RESOLUTION = "1D";
const APY_LIMIT = 5000;
const PAGE_SIZE = 15;
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
const CURATOR_ALLOCATION_WINDOWS = [
  { label: "1M", days: 30 },
  { label: "1Y", days: 365 },
  { label: "ALL", days: null },
];
const MORPHO_CURATOR_TOP_N = 15;
const CURATOR_COLORS = [
  "#22d3ee", "#34d399", "#818cf8", "#fb7185", "#facc15",
  "#f97316", "#a78bfa", "#38bdf8", "#4ade80", "#f472b6",
  "#e879f9", "#fbbf24", "#2dd4bf", "#c084fc", "#64748b",
  "#94a3b8",
];

const PROTOCOL_WEBSITES = {
  AAVE_MARKET: "https://aave.com",
  SPARK_MARKET: "https://spark.fi",
  MORPHO_MARKET: "https://morpho.org",
  FLUID_MARKET: "https://fluid.instadapp.io",
  EULER_MARKET: "https://euler.finance",
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
  name: "Borrow APY",
  format: "percent",
};
const SECONDARY_BORROW_APY_AREA = {
  key: "secondaryBorrowApy",
  color: "#34d399",
  name: "Borrow APY",
  format: "percent",
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
  name: "Debt / Collateral",
  format: "percent",
};

const TABLE_MODES = [
  { key: "vaults", label: "VAULTS" },
  { key: "markets", label: "MARKETS" },
];

const MARKET_COLUMNS = [
  { key: "netWorth", label: "Liquidity" },
  { key: "supplyUsd", label: "Total Supply" },
  { key: "borrowUsd", label: "Total Borrow" },
  { key: "supplyApy", label: "Supply APY" },
  { key: "borrowApy", label: "Borrow APY" },
  { key: "utilization", label: "Utilization" },
  { key: "lltv", label: "LLTV" },
];

const VAULT_COLUMNS = [
  { key: "supplyUsd", label: "TVL" },
  { key: "assetSymbol", label: "Asset" },
  { key: "sharePriceUsd", label: "Share price" },
  { key: "supplyApy", label: "30D APY" },
  { key: "exposure", label: "Exposure" },
  { key: "curator", label: "Curator" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const finiteNumber = (value, fallback = 0) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const isMorphoMarketId = (value) => /^(0x)?[0-9a-fA-F]{64}$/.test(String(value || ""));
const isVaultAddress = (value) => /^(0x)?[0-9a-fA-F]{40}$/.test(String(value || ""));

const formatCurrency = (value) => {
  const a = finiteNumber(value);
  if (a >= 1e9) return `$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(a / 1e3).toFixed(0)}K`;
  return `$${a.toFixed(0)}`;
};

const formatApy = (v) => `${(finiteNumber(v) * 100).toFixed(2)}%`;

const formatPercent = (v, d = 2) => `${(finiteNumber(v) * 100).toFixed(d)}%`;

const formatLltvRange = (row) => {
  const min = finiteNumber(row?.lltvMin, NaN);
  const max = finiteNumber(row?.lltvMax, NaN);
  if (Number.isFinite(min) && Number.isFinite(max) && max > 0) {
    if (Math.abs(max - min) < 0.000001) return formatPercent(max);
    return `${formatPercent(min)}-${formatPercent(max)}`;
  }
  const lltv = finiteNumber(row?.lltv, NaN);
  return Number.isFinite(lltv) && lltv > 0 ? formatPercent(lltv) : "-";
};

const formatAssetSharePrice = (value, symbol) => {
  const n = finiteNumber(value);
  if (n <= 0) return "-";
  const suffix = symbol ? ` ${symbol}` : "";
  if (n >= 1000) return `${n.toLocaleString(undefined, { maximumFractionDigits: 3, minimumFractionDigits: 3 })}${suffix}`;
  return `${n.toFixed(3)}${suffix}`;
};

const shortAddress = (value) => {
  const text = String(value || "");
  if (text.length <= 12) return text || "-";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
};

const formatSignedCurrency = (value) => {
  const a = finiteNumber(value);
  if (Math.abs(a) < 1) return "$0";
  const sign = a < 0 ? "-" : "+";
  return `${sign}${formatCurrency(Math.abs(a))}`;
};

const protocolGroupFromKey = (key) => {
  if (!key) return "";
  const normalized = String(key).replace("_MARKET", "").toUpperCase();
  if (normalized === "COMPOUND V3") return "COMPOUND_V3";
  return normalized;
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

const groupAssetItems = (items) => {
  const totals = new Map();
  items.forEach(({ symbol, value }) => {
    const normalizedSymbol = String(symbol || "UNKNOWN").trim() || "UNKNOWN";
    totals.set(
      normalizedSymbol,
      (totals.get(normalizedSymbol) || 0) + Math.max(0, finiteNumber(value)),
    );
  });
  return [...totals.entries()].map(([symbol, value]) => ({ symbol, value }));
};

// ---------------------------------------------------------------------------
// Asset Breakdown Bar (stacked horizontal bar + legend)
// ---------------------------------------------------------------------------

function AssetBreakdownBar({ items = [], title = "", loading = false }) {
  const [tooltip, setTooltip] = useState(null);
  const containerRef = useRef(null);
  const valueLabel = title === "DEBT MIX" ? "Debt" : "Supply";

  const data = useMemo(() => {
    if (!items.length) return { segments: [], total: 0, count: 0 };
    const sorted = [...items]
      .filter((m) => m.value > 0)
      .sort((a, b) => b.value - a.value);
    const total = sorted.reduce((s, m) => s + m.value, 0);
    if (total === 0) return { segments: [], total: 0, count: 0 };

    const maxSegments = 8;
    const hasOther = sorted.length > maxSegments;
    const top = sorted.slice(0, hasOther ? maxSegments - 1 : maxSegments);
    const rest = hasOther ? sorted.slice(maxSegments - 1) : [];
    const otherValue = rest.reduce((s, m) => s + m.value, 0);
    if (otherValue > 0) top.push({ symbol: "Other", value: otherValue });

    return {
      segments: top.map((m) => ({
        symbol: m.symbol,
        value: m.value,
        pct: m.value / total,
        color: getTokenColor(m.symbol),
      })),
      total,
      count: sorted.length,
    };
  }, [items]);

  const handleMouseMove = (e, seg) => {
    if (!containerRef.current) return;
    const parentRect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - parentRect.left;
    const y = e.clientY - parentRect.top;

    // Shift tooltip to the left if hovering near the right edge to avoid overflow
    const adjustX = x > parentRect.width - 160 ? x - 170 : x + 15;

    setTooltip({ x: adjustX, y: y + 15, seg });
  };

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
    <div className="relative" ref={containerRef}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] md:text-xs text-gray-400 uppercase tracking-widest font-bold">
          {title}
        </span>
        <span className="text-[10px] md:text-xs text-gray-500 uppercase tracking-widest">
          {data.count} Assets
        </span>
      </div>
      {/* Stacked bar */}
      <div
        className="w-full h-9 flex rounded-sm overflow-hidden bg-[#111]"
        onMouseLeave={() => setTooltip(null)}
      >
        {data.segments.map((seg) => (
          <div
            key={seg.symbol}
            className="h-full transition-all duration-700 hover:opacity-100 cursor-default"
            style={{
              width: `${(seg.pct * 100).toFixed(2)}%`,
              backgroundColor: seg.color,
              opacity: 0.6,
              minWidth: seg.pct > 0.005 ? "2px" : "0",
            }}
            onMouseMove={(e) => handleMouseMove(e, seg)}
          />
        ))}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-x-6 gap-y-1.5 mt-3">
        {data.segments.map((seg) => (
          <div key={seg.symbol} className="flex items-center gap-2">
            <div
              className="w-2.5 h-2.5 rounded-[1px]"
              style={{ backgroundColor: seg.color }}
            />
            <span className="text-[10px] md:text-[11px] text-gray-400 uppercase tracking-widest font-mono">
              {seg.symbol}
            </span>
            <span className="text-[10px] md:text-[11px] text-white font-bold font-mono tracking-wider">
              {(seg.pct * 100).toFixed(1)}%
            </span>
            <span className="text-[10px] md:text-[11px] text-gray-500 font-mono tracking-wider">
              {formatCurrency(seg.value)}
            </span>
          </div>
        ))}
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none rounded-sm border border-zinc-800 bg-[#0a0a0a] px-3 py-2 text-xs font-mono shadow-2xl"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2 border-b border-white/10 pb-1.5 mb-0.5">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: tooltip.seg.color }} />
              <span className="font-bold text-white uppercase tracking-wider">{tooltip.seg.symbol}</span>
            </div>
            <div className="flex justify-between gap-6">
              <span className="text-gray-500 uppercase tracking-widest text-[10px]">{valueLabel}</span>
              <span className="text-white">{formatCurrency(tooltip.seg.value)}</span>
            </div>
            <div className="flex justify-between gap-6">
              <span className="text-gray-500 uppercase tracking-widest text-[10px]">Share</span>
              <span className="text-white">{(tooltip.seg.pct * 100).toFixed(1)}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3-Column Protocol Alluvial Flow Chart
// ---------------------------------------------------------------------------

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
  const total = items.reduce((s, item) => s + finiteNumber(totals.get(item)), 0);
  const avail = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawH = items.map((item) => (total <= 0 ? avail / items.length : (avail * finiteNumber(totals.get(item))) / total));
  const visH = rawH.map((h) => Math.max(minH, h));
  const visTotal = visH.reduce((s, h) => s + h, 0);
  const scale = visTotal > avail ? avail / visTotal : 1;
  const heights = visH.map((h) => Math.max(10, h * scale));
  const used = heights.reduce((s, h) => s + h, 0) + gap * (items.length - 1);
  let y = top + Math.max(0, (height - top - bottom - used) / 2);
  items.forEach((item, i) => {
    const h = heights[i];
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
    // Aggregate by asset
    const assetTotals = sumFlowBy(flows, "asset");
    const topAssets = new Set(
      [...assetTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([a]) => a),
    );
    const rows = [];
    const grouped = new Map();
    flows.forEach((f) => {
      const dir = flowDir(f.action);
      if (!dir) return;
      const asset = topAssets.has(f.asset) ? f.asset : "Other";
      const key = `${asset}|${dir}`;
      const cur = grouped.get(key) || { asset, inflowUsd: 0, outflowUsd: 0 };
      const val = finiteNumber(f.valueUsd);
      if (dir === "Inflow") cur.inflowUsd += val;
      else cur.outflowUsd += val;
      grouped.set(key, cur);
    });
    [...grouped.values()].forEach((row) => {
      if (row.inflowUsd > 0) rows.push({ protocol: protocolName, asset: row.asset, direction: "Inflow", valueUsd: row.inflowUsd });
      if (row.outflowUsd > 0) rows.push({ protocol: protocolName, asset: row.asset, direction: "Outflow", valueUsd: row.outflowUsd });
    });

    const inflowRows = rows.filter((r) => r.direction === "Inflow");
    const outflowRows = rows.filter((r) => r.direction === "Outflow");
    const inflowAssetTotals = sumFlowBy(inflowRows, "asset");
    const outflowAssetTotals = sumFlowBy(outflowRows, "asset");
    const protocolFlowTotals = new Map([[protocolName, rows.reduce((s, r) => s + r.valueUsd, 0)]]);
    const protocolInflowTotals = new Map([[protocolName, inflowRows.reduce((s, r) => s + r.valueUsd, 0)]]);
    const protocolOutflowTotals = new Map([[protocolName, outflowRows.reduce((s, r) => s + r.valueUsd, 0)]]);
    const netDelta = (protocolInflowTotals.get(protocolName) || 0) - (protocolOutflowTotals.get(protocolName) || 0);
    const inflowAssets = [...inflowAssetTotals.keys()].sort(sortFlowBy(inflowAssetTotals));
    const outflowAssets = [...outflowAssetTotals.keys()].sort(sortFlowBy(outflowAssetTotals));
    return {
      rows, inflowRows, outflowRows,
      inflowAssetTotals, outflowAssetTotals,
      protocolFlowTotals, protocolInflowTotals, protocolOutflowTotals,
      inflowAssets, outflowAssets, netDelta,
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
  const xProto = 555;
  const xOutflow = 930;
  const headerY = 14;

  const inflowLayout = proportionalSlots(model.inflowAssets, model.inflowAssetTotals, top + 22, bottom + 8, height, 10, 9);
  const protoLayout = proportionalSlots([protocolName], model.protocolFlowTotals, top + 64, bottom + 38, height, 40, 0);
  const outflowLayout = proportionalSlots(model.outflowAssets, model.outflowAssetTotals, top + 22, bottom + 8, height, 10, 9);

  const ribbonPath = (x1, src, x2, tgt) => {
    const mid = (x1 + x2) / 2;
    return [
      `M ${x1.toFixed(2)} ${src.y0.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${src.y0.toFixed(2)}, ${mid.toFixed(2)} ${tgt.y0.toFixed(2)}, ${x2.toFixed(2)} ${tgt.y0.toFixed(2)}`,
      `L ${x2.toFixed(2)} ${tgt.y1.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${tgt.y1.toFixed(2)}, ${mid.toFixed(2)} ${src.y1.toFixed(2)}, ${x1.toFixed(2)} ${src.y1.toFixed(2)}`,
      "Z",
    ].join(" ");
  };
  const linkSeg = (layout, totals, offsets, key, value) => {
    const slot = layout.get(key);
    if (!slot) return null;
    const total = Math.max(1, finiteNumber(totals.get(key)));
    const dy = (slot.h * finiteNumber(value)) / total;
    const offset = offsets.get(key) || 0;
    offsets.set(key, offset + dy);
    return { y0: slot.y + offset, y1: slot.y + offset + dy };
  };

  const iaOff = new Map();
  const oaOff = new Map();
  const piOff = new Map();
  const poOff = new Map();
  const showTip = (e, link) => {
    const b = e.currentTarget.ownerSVGElement.getBoundingClientRect();
    const x = e.clientX - b.left;
    const y = e.clientY - b.top;

    // Shift tooltip to the left if hovering near the right edge to avoid overflow
    const adjustX = x > b.width - 160 ? x - 170 : x + 15;

    setTooltip({ x: adjustX, y: y + 15, link });
  };

  const inflowLinks = model.inflowRows.sort((a, b) => b.valueUsd - a.valueUsd).map((row) => {
    const src = linkSeg(inflowLayout, model.inflowAssetTotals, iaOff, row.asset, row.valueUsd);
    const tgt = linkSeg(protoLayout, model.protocolInflowTotals, piOff, protocolName, row.valueUsd);
    if (!src || !tgt) return null;
    return { d: ribbonPath(xInflow + nodeWidth, src, xProto, tgt), color: FLOW_COLORS.Inflow, sourceName: row.asset, targetName: protocolName, valueUsd: row.valueUsd };
  }).filter(Boolean);

  const outflowLinks = model.outflowRows.sort((a, b) => b.valueUsd - a.valueUsd).map((row) => {
    const src = linkSeg(protoLayout, model.protocolOutflowTotals, poOff, protocolName, row.valueUsd);
    const tgt = linkSeg(outflowLayout, model.outflowAssetTotals, oaOff, row.asset, row.valueUsd);
    if (!src || !tgt) return null;
    return { d: ribbonPath(xProto + nodeWidth, src, xOutflow, tgt), color: FLOW_COLORS.Outflow, sourceName: protocolName, targetName: row.asset, valueUsd: row.valueUsd };
  }).filter(Boolean);

  const renderNodes = (items, totals, slots, x, anchor, fallbackColor) => items.map((item) => {
    const slot = slots.get(item);
    if (!slot) return null;
    const color = item === protocolName ? "#998EFF" : fallbackColor;
    const labelX = anchor === "end" ? x - 8 : anchor === "middle" ? x + nodeWidth / 2 : x + nodeWidth + 8;
    const isProto = anchor === "middle";
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={color} rx="2" />
        {isProto ? (
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
          {[...inflowLinks, ...outflowLinks].map((link, i) => (
            <path
              key={i}
              d={link.d}
              fill={link.color}
              fillOpacity="0.32"
              stroke="none"
              onMouseMove={(e) => showTip(e, link)}
              onMouseLeave={() => setTooltip(null)}
              className="transition-opacity hover:opacity-80 cursor-default"
            />
          ))}
          <text x={xInflow + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET INFLOWS</text>
          <text x={xProto + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">PROTOCOL</text>
          <text x={xOutflow + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET OUTFLOWS</text>
          {renderNodes(model.inflowAssets, model.inflowAssetTotals, inflowLayout, xInflow, "end", FLOW_COLORS.Inflow)}
          {renderNodes([protocolName], model.protocolFlowTotals, protoLayout, xProto, "middle")}
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
              <span className="font-bold text-white uppercase tracking-wider">{tooltip.link.sourceName} → {tooltip.link.targetName}</span>
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

function MorphoCuratorFlowChart({
  flows = [],
  loading = false,
  protocolName = "Morpho",
  channelLabel = "CHANNELS",
}) {
  const [tooltip, setTooltip] = useState(null);
  const protocolNode = protocolName || "Protocol";
  const directNode = "Direct";
  const otherNode = "Other";
  const protocolColor = "#2973FF";
  const curatorColor = "#6b7280";
  const model = useMemo(() => {
    if (!flows.length) return null;
    const curatorInTotals = new Map();
    const curatorOutTotals = new Map();
    const curatorLayoutTotals = new Map();
    const inflowAssetTotals = new Map();
    const outflowAssetTotals = new Map();
    const morphoTotals = new Map();
    const inflowRows = [];
    const outflowRows = [];
    flows.forEach((row) => {
      const valueUsd = finiteNumber(row.valueUsd);
      if (valueUsd <= 0) return;
      const rawCurator = row.curator || otherNode;
      const curator = rawCurator === "Unassigned" || rawCurator === "Unassigned Vaults" ? otherNode : rawCurator;
      const asset = row.asset || "UNKNOWN";
      if (row.action === "Net Outflow") {
        curatorOutTotals.set(curator, (curatorOutTotals.get(curator) || 0) + valueUsd);
        outflowAssetTotals.set(asset, (outflowAssetTotals.get(asset) || 0) + valueUsd);
        outflowRows.push({ curator, asset, valueUsd });
      } else {
        curatorInTotals.set(curator, (curatorInTotals.get(curator) || 0) + valueUsd);
        inflowAssetTotals.set(asset, (inflowAssetTotals.get(asset) || 0) + valueUsd);
        inflowRows.push({ curator, asset, valueUsd });
      }
    });
    const allCurators = new Set([...curatorInTotals.keys(), ...curatorOutTotals.keys()]);
    allCurators.forEach((curator) => {
      curatorLayoutTotals.set(
        curator,
        Math.max(
          finiteNumber(curatorInTotals.get(curator)),
          finiteNumber(curatorOutTotals.get(curator)),
        ),
      );
    });
    const sortedCurators = [...allCurators]
      .filter((curator) => curator !== directNode)
      .sort(sortFlowBy(curatorLayoutTotals));
    const curators = allCurators.has(directNode)
      ? [directNode, ...sortedCurators.slice(0, 9)]
      : sortedCurators.slice(0, 10);
    const curatorSet = new Set(curators);
    const visibleInflowRows = inflowRows.filter((row) => curatorSet.has(row.curator));
    const visibleOutflowRows = outflowRows.filter((row) => curatorSet.has(row.curator));
    const visibleInflowTotal = visibleInflowRows.reduce((sum, row) => sum + row.valueUsd, 0);
    const visibleOutflowTotal = visibleOutflowRows.reduce((sum, row) => sum + row.valueUsd, 0);
    const visibleInflowAssetTotals = new Map();
    const visibleOutflowAssetTotals = new Map();
    const curatorNetTotals = new Map();
    visibleInflowRows.forEach((row) => {
      visibleInflowAssetTotals.set(
        row.asset,
        (visibleInflowAssetTotals.get(row.asset) || 0) + row.valueUsd,
      );
      curatorNetTotals.set(row.curator, (curatorNetTotals.get(row.curator) || 0) + row.valueUsd);
    });
    visibleOutflowRows.forEach((row) => {
      visibleOutflowAssetTotals.set(
        row.asset,
        (visibleOutflowAssetTotals.get(row.asset) || 0) + row.valueUsd,
      );
      curatorNetTotals.set(row.curator, (curatorNetTotals.get(row.curator) || 0) - row.valueUsd);
    });
    morphoTotals.set(protocolNode, Math.max(visibleInflowTotal, visibleOutflowTotal, 1));
    return {
      curators,
      curatorInTotals,
      curatorOutTotals,
      curatorLayoutTotals,
      curatorNetTotals,
      morphoTotals,
      inflowRows: visibleInflowRows,
      outflowRows: visibleOutflowRows,
      inflowAssets: [...visibleInflowAssetTotals.keys()].sort(sortFlowBy(visibleInflowAssetTotals)),
      outflowAssets: [...visibleOutflowAssetTotals.keys()].sort(sortFlowBy(visibleOutflowAssetTotals)),
      inflowAssetTotals: visibleInflowAssetTotals,
      outflowAssetTotals: visibleOutflowAssetTotals,
    };
  }, [flows, directNode, otherNode, protocolNode]);

  if (loading && !flows.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!model || !model.curators.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-600 uppercase tracking-widest">
        No flow data available
      </div>
    );
  }

  const width = 1120;
  const height = 390;
  const nodeWidth = 12;
  const xInflow = 120;
  const xMorpho = 390;
  const xCurator = 665;
  const xOutflow = 980;
  const inflowLayout = proportionalSlots(model.inflowAssets, model.inflowAssetTotals, 44, 24, height, 10, 10);
  const morphoLayout = proportionalSlots([protocolNode], model.morphoTotals, 70, 24, height, 14, 12);
  const curatorLayout = proportionalSlots(model.curators, model.curatorLayoutTotals, 36, 24, height, 14, 18);
  const outflowLayout = proportionalSlots(model.outflowAssets, model.outflowAssetTotals, 44, 24, height, 10, 10);

  const ribbonPath = (x1, src, x2, tgt) => {
    const mid = (x1 + x2) / 2;
    return [
      `M ${x1.toFixed(2)} ${src.y0.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${src.y0.toFixed(2)}, ${mid.toFixed(2)} ${tgt.y0.toFixed(2)}, ${x2.toFixed(2)} ${tgt.y0.toFixed(2)}`,
      `L ${x2.toFixed(2)} ${tgt.y1.toFixed(2)}`,
      `C ${mid.toFixed(2)} ${tgt.y1.toFixed(2)}, ${mid.toFixed(2)} ${src.y1.toFixed(2)}, ${x1.toFixed(2)} ${src.y1.toFixed(2)}`,
      "Z",
    ].join(" ");
  };
  const linkSeg = (layout, totals, offsets, key, value) => {
    const slot = layout.get(key);
    if (!slot) return null;
    const total = Math.max(1, finiteNumber(totals.get(key)));
    const dy = (slot.h * finiteNumber(value)) / total;
    const offset = offsets.get(key) || 0;
    offsets.set(key, offset + dy);
    return { y0: slot.y + offset, y1: slot.y + offset + dy };
  };

  const inflowOffsets = new Map();
  const morphoInOffsets = new Map();
  const morphoOutOffsets = new Map();
  const curatorOutOffsets = new Map();
  const curatorInOffsets = new Map();
  const outflowOffsets = new Map();
  const inflowLinks = [...model.inflowAssetTotals.entries()].sort((a, b) => b[1] - a[1]).map(([asset, valueUsd]) => {
    const src = linkSeg(inflowLayout, model.inflowAssetTotals, inflowOffsets, asset, valueUsd);
    const tgt = linkSeg(morphoLayout, model.morphoTotals, morphoInOffsets, protocolNode, valueUsd);
    if (!src || !tgt) return null;
    return { d: ribbonPath(xInflow + nodeWidth, src, xMorpho, tgt), color: FLOW_COLORS.Inflow, sourceName: asset, targetName: protocolNode, valueUsd };
  }).filter(Boolean);
  const morphoLinks = [...model.curatorInTotals.entries()]
    .filter(([curator]) => model.curators.includes(curator))
    .sort((a, b) => (a[0] === directNode ? -1 : b[0] === directNode ? 1 : b[1] - a[1]))
    .map(([curator, valueUsd]) => {
      const src = linkSeg(morphoLayout, model.morphoTotals, morphoOutOffsets, protocolNode, valueUsd);
      const tgt = linkSeg(curatorLayout, model.curatorLayoutTotals, curatorInOffsets, curator, valueUsd);
      if (!src || !tgt) return null;
      return {
        d: ribbonPath(xMorpho + nodeWidth, src, xCurator, tgt),
        color: curatorColor,
        sourceName: protocolNode,
        targetName: curator,
        valueUsd,
      };
    }).filter(Boolean);
  const outflowLinks = model.outflowRows.sort((a, b) => b.valueUsd - a.valueUsd).map((row) => {
    const src = linkSeg(curatorLayout, model.curatorLayoutTotals, curatorOutOffsets, row.curator, row.valueUsd);
    const tgt = linkSeg(outflowLayout, model.outflowAssetTotals, outflowOffsets, row.asset, row.valueUsd);
    if (!src || !tgt) return null;
    return { d: ribbonPath(xCurator + nodeWidth, src, xOutflow, tgt), color: FLOW_COLORS.Outflow, sourceName: row.curator, targetName: row.asset, valueUsd: row.valueUsd, signedValueUsd: -row.valueUsd };
  }).filter(Boolean);

  const showTip = (e, link) => {
    const b = e.currentTarget.ownerSVGElement.getBoundingClientRect();
    const x = e.clientX - b.left;
    const y = e.clientY - b.top;
    setTooltip({ x: x > b.width - 170 ? x - 180 : x + 15, y: y + 15, link });
  };
  const nodeColor = (item, color) => {
    return typeof color === "function" ? color(item) : color;
  };
  const renderNodes = (items, totals, slots, x, anchor, color, labelPlacement = "center", valueFormatter = formatCurrency) => items.map((item) => {
    const slot = slots.get(item);
    if (!slot) return null;
    const isAbove = labelPlacement === "above";
    const labelX = isAbove ? x + nodeWidth / 2 : anchor === "end" ? x - 8 : anchor === "middle" ? x + nodeWidth / 2 : x + nodeWidth + 8;
    const labelY = isAbove ? Math.max(22, slot.y - 6) : slot.y + slot.h / 2 + 4;
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={nodeColor(item, color)} rx="2" />
        <text x={labelX} y={labelY} textAnchor={isAbove ? "middle" : anchor} fill="#e5e7eb" fontSize="12">
          {`${item} ${valueFormatter(totals.get(item))}`}
        </text>
      </g>
    );
  });

  return (
    <div className="relative w-full h-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full" role="img" aria-label={`${protocolNode} channel flow alluvial chart`}>
        <g>
          {[...inflowLinks, ...morphoLinks, ...outflowLinks].map((link, i) => (
            <path
              key={i}
              d={link.d}
              fill={link.color}
              fillOpacity="0.32"
              stroke="none"
              onMouseMove={(e) => showTip(e, link)}
              onMouseLeave={() => setTooltip(null)}
              className="transition-opacity hover:opacity-80 cursor-default"
            />
          ))}
          <text x={xInflow + nodeWidth / 2} y={14} textAnchor="middle" fill="#6b7280" fontSize="10">NET INFLOWS</text>
          <text x={xMorpho + nodeWidth / 2} y={14} textAnchor="middle" fill="#6b7280" fontSize="10">{String(protocolNode).toUpperCase()}</text>
          <text x={xCurator + nodeWidth / 2} y={14} textAnchor="middle" fill="#6b7280" fontSize="10">{channelLabel}</text>
          <text x={xOutflow + nodeWidth / 2} y={14} textAnchor="middle" fill="#6b7280" fontSize="10">NET OUTFLOWS</text>
          {renderNodes(model.inflowAssets, model.inflowAssetTotals, inflowLayout, xInflow, "end", FLOW_COLORS.Inflow)}
          {renderNodes([protocolNode], model.morphoTotals, morphoLayout, xMorpho, "middle", protocolColor, "above")}
          {renderNodes(model.curators, model.curatorNetTotals, curatorLayout, xCurator, "middle", curatorColor, "above", formatSignedCurrency)}
          {renderNodes(model.outflowAssets, model.outflowAssetTotals, outflowLayout, xOutflow, "start", FLOW_COLORS.Outflow, "center", (value) => formatSignedCurrency(-finiteNumber(value)))}
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
              <span className="text-white">{tooltip.link.signedValueUsd == null ? formatCurrency(tooltip.link.valueUsd) : formatSignedCurrency(tooltip.link.signedValueUsd)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CuratorAllocationTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const date = new Date(label * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  return (
    <div className="bg-[#0a0a0a] border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50 min-w-[240px]">
      <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">{date}</p>
      {[...payload]
        .filter((entry) => finiteNumber(entry.value) > 0)
        .sort((a, b) => finiteNumber(b.value) - finiteNumber(a.value))
        .slice(0, 12)
        .map((entry) => (
          <div key={entry.dataKey} className="flex items-center justify-between gap-4 mb-1">
            <span className="font-bold truncate max-w-[150px]" style={{ color: entry.color }}>{entry.name}</span>
            <span className="text-white">{formatCurrency(entry.value)}</span>
          </div>
        ))}
    </div>
  );
}

function CuratorAllocationChart({ data = [], curators = [], loading = false }) {
  if (loading && !data.length) {
    return (
      <div className="h-[360px] w-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!data.length || !curators.length) {
    return (
      <div className="h-[360px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
        No curator allocation history
      </div>
    );
  }
  return (
    <div className="h-[360px] w-full">
      <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>
        <AreaChart data={data} margin={{ top: 8, right: 34, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" />
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
            width={70}
          />
          <Tooltip content={<CuratorAllocationTooltip />} />
          {curators.map((curator, index) => (
            <Area
              key={curator}
              type="monotone"
              dataKey={curator}
              name={curator}
              stackId="curator"
              fill={CURATOR_COLORS[index % CURATOR_COLORS.length]}
              fillOpacity={0.7}
              stroke={CURATOR_COLORS[index % CURATOR_COLORS.length]}
              strokeWidth={0}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page Component
// ---------------------------------------------------------------------------

export default function AaveProtocolPage() {
  const { protocol: protocolSlug } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // Explicit routes like /data/aave have no :protocol param — derive from path.
  const resolvedSlug =
    protocolSlug || location.pathname.split("/").filter(Boolean)[1] || "aave";
  const protocolKey = apiProtocolForSlug(resolvedSlug);
  const rawDisplayName = getProtocolDisplayName(protocolKey);
  const displayName = rawDisplayName;
  const isMorpho = protocolKey === "MORPHO_MARKET";
  const isEuler = protocolKey === "EULER_MARKET";
  const isCompoundV3 = protocolKey === "COMPOUND_V3_MARKET";
  const hasChannelFlows = isMorpho || isEuler;
  const maxBorrowApy = isMorpho ? 1 : null;
  const rateAssetSymbols = useMemo(
    () => (
      protocolKey === "COMPOUND_V3_MARKET"
        ? ["USDC", "WETH"]
        : protocolKey === "SPARK_MARKET"
          ? ["USDT", "USDS"]
          : ["USDC", "USDT"]
    ),
    [protocolKey],
  );
  const [primaryRateSymbol, secondaryRateSymbol] = rateAssetSymbols;

  // --- State ---
  const [currentPage, setCurrentPage] = useState(1);
  const [sortKey, setSortKey] = useState("supplyUsd");
  const [sortDir, setSortDir] = useState("desc");
  const [tableMode, setTableMode] = useState("vaults");
  const [flowWindowDays, setFlowWindowDays] = useState(7);
  const [balanceWindowDays, setBalanceWindowDays] = useState(null);
  const [rateWindowDays, setRateWindowDays] = useState(365);
  const [utilizationWindowDays, setUtilizationWindowDays] = useState(365);
  const [curatorAllocationWindowDays, setCuratorAllocationWindowDays] = useState(null);
  const activeFlowWindow = FLOW_WINDOWS.find((window) => window.days === flowWindowDays) || FLOW_WINDOWS[1];

  // --- Data Fetching ---
  const compoundV3PageKey = useMemo(
    () => (
      isCompoundV3
        ? queryKeys.apiCompoundV3ProtocolPage(
          API_GRAPHQL_URL,
          flowWindowDays,
          APY_LIMIT,
          rateAssetSymbols,
        )
        : null
    ),
    [flowWindowDays, isCompoundV3, rateAssetSymbols],
  );
  const {
    data: compoundV3PageGql,
    isLoading: compoundV3PageLoading,
  } = useSWR(
    compoundV3PageKey,
    ([, , variables]) =>
      apiGraphQL("CompoundV3ProtocolPage", {
        query: COMPOUND_V3_PROTOCOL_PAGE_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: marketsGql, isLoading: marketsLoading } = useSWR(
    !isCompoundV3 ? queryKeys.apiProtocolMarkets(API_GRAPHQL_URL, protocolKey, maxBorrowApy) : null,
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
    isMorpho ? queryKeys.apiMetaMorphoVaults(API_GRAPHQL_URL, 2000) : null,
    ([, , variables]) =>
      apiGraphQL("MetaMorphoVaults", {
        query: METAMORPHO_VAULTS_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: apyGql, isLoading: apyLoading } = useSWR(
    !isCompoundV3 ? queryKeys.apiProtocolApyHistory(
      API_GRAPHQL_URL,
      protocolKey,
      APY_RESOLUTION,
      APY_LIMIT,
      maxBorrowApy,
    ) : null,
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
    !isCompoundV3 ? queryKeys.apiProtocolAssetApyHistory(
      API_GRAPHQL_URL,
      protocolKey,
      rateAssetSymbols,
      APY_RESOLUTION,
      APY_LIMIT * 2,
      maxBorrowApy,
    ) : null,
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

  // Fetch selected-window alluvial flows from the lending hub page (cached, shared)
  const { data: flowsGql } = useSWR(
    !isCompoundV3 ? queryKeys.apiLendingPage(API_GRAPHQL_URL, "USD", flowWindowDays) : null,
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

  const { data: channelFlowsGql, isLoading: channelFlowsLoading } = useSWR(
    isMorpho
      ? queryKeys.apiMorphoCuratorFlows(
        API_GRAPHQL_URL,
        flowWindowDays,
        10,
        maxBorrowApy,
      )
      : isEuler
        ? queryKeys.apiEulerChannelFlows(
          API_GRAPHQL_URL,
          flowWindowDays,
          10,
          maxBorrowApy,
        )
        : null,
    ([, , variables]) =>
      apiGraphQL(isEuler ? "EulerChannelFlows" : "MorphoCuratorFlows", {
        query: isEuler ? EULER_CHANNEL_FLOWS_QUERY : MORPHO_CURATOR_FLOWS_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const { data: curatorAllocationGql, isLoading: curatorAllocationLoading } = useSWR(
    isMorpho
      ? queryKeys.apiMorphoCuratorAllocationHistory(
        API_GRAPHQL_URL,
        APY_RESOLUTION,
        APY_LIMIT,
        MORPHO_CURATOR_TOP_N,
        maxBorrowApy,
      )
      : null,
    ([, , variables]) =>
      apiGraphQL("MorphoCuratorAllocationHistory", {
        query: MORPHO_CURATOR_ALLOCATION_HISTORY_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  // --- Derived Data ---
  const stats = useMemo(() => {
    const s = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.stats
      : marketsGql?.protocolMarketsPage?.stats;
    return {
      totalSupply: s?.totalSupplyUsd || 0,
      totalBorrow: s?.totalBorrowUsd || 0,
      avgSupplyApy: s?.averageSupplyApy || 0,
      avgBorrowApy: s?.averageBorrowApy || 0,
      avgUtil: s?.averageUtilization || 0,
      count: s?.marketCount || 0,
    };
  }, [compoundV3PageGql, isCompoundV3, marketsGql]);

  const markets = useMemo(() => {
    const rows = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.rows || []
      : marketsGql?.protocolMarketsPage?.rows || [];
    return rows
      .filter((r) => !isMorpho || isMorphoMarketId(r.entityId))
      .map((r) => ({
        rowType: "market",
        entityId: r.entityId,
        symbol: r.symbol,
        marketLabel: r.collateralSymbol ? `${r.collateralSymbol}-${r.symbol}` : r.symbol,
        marketSubLabel: r.collateralSymbol ? "Collateral-Debt" : r.symbol,
        collateralSymbol: r.collateralSymbol || null,
        protocol: r.protocol,
        supplyUsd: r.supplyUsd || 0,
        borrowUsd: r.borrowUsd || 0,
        netWorth: (r.supplyUsd || 0) - (r.borrowUsd || 0),
        supplyApy: r.supplyApy || 0,
      borrowApy: r.borrowApy || 0,
      utilization: r.utilization || 0,
      lltv: r.lltv || 0,
      lltvMin: r.lltvMin,
      lltvMax: r.lltvMax,
      isTrapped: Boolean(r.isTrapped),
      loanIcon: getTokenIcon(r.symbol),
        collateralIcon: r.collateralSymbol ? getTokenIcon(r.collateralSymbol) : null,
      }));
  }, [compoundV3PageGql, isCompoundV3, isMorpho, marketsGql]);

  const vaults = useMemo(() => {
    const rows = vaultsGql?.metamorphoVaults || [];
    const seenVaultAddresses = new Set();
    return rows
      .filter((r) => isVaultAddress(r.vaultAddress) && finiteNumber(r.tvlUsd) > 0)
      .filter((r) => {
        const address = String(r.vaultAddress || "").toLowerCase();
        if (seenVaultAddresses.has(address)) return false;
        seenVaultAddresses.add(address);
        return true;
      })
      .map((r) => {
        const assetSymbol = String(r.assetSymbol || "").trim() || "UNKNOWN";
        const label = String(r.name || "").trim() || shortAddress(r.vaultAddress);
        const exposure = (r.exposure || [])
          .map((item) => ({
            symbol: String(item?.symbol || "").trim(),
            valueUsd: finiteNumber(item?.valueUsd),
          }))
          .filter((item) => item.symbol && item.valueUsd > 0)
          .sort((a, b) => b.valueUsd - a.valueUsd);
        const vaultApy = r.apy === null || r.apy === undefined || r.apy === ""
          ? null
          : Number.isFinite(Number(r.apy))
            ? finiteNumber(r.apy)
            : null;
        return {
          rowType: "vault",
          entityId: r.vaultAddress,
          vaultAddress: r.vaultAddress,
          vaultAddressShort: shortAddress(r.vaultAddress),
          symbol: label,
          marketLabel: label,
          assetSymbol,
          protocol: protocolKey,
          supplyUsd: finiteNumber(r.tvlUsd),
          borrowUsd: 0,
          netWorth: finiteNumber(r.tvlUsd),
          supplyApy: vaultApy,
          borrowApy: null,
          utilization: null,
          sharePriceUsd: finiteNumber(r.sharePriceUsd),
          sharePriceAssets: r.sharePriceAssets === null || r.sharePriceAssets === undefined || r.sharePriceAssets === ""
            ? null
            : finiteNumber(r.sharePriceAssets),
          exposure,
          curator: String(r.curator || "").trim() || "Other",
          curatorAddress: String(r.curatorAddress || "").trim(),
          isCanonicalTvl: Boolean(r.isCanonicalTvl),
          lastSnapshotTimestamp: finiteNumber(r.lastSnapshotTimestamp),
          loanIcon: getTokenIcon(assetSymbol),
          collateralIcon: null,
        };
      });
  }, [vaultsGql, protocolKey]);

  const stablecoinBorrowApyAreas = useMemo(
    () => [
      {
        ...PRIMARY_BORROW_APY_AREA,
        name: `${primaryRateSymbol} Borrow APY`,
      },
      {
        ...SECONDARY_BORROW_APY_AREA,
        name: `${secondaryRateSymbol} Borrow APY`,
      },
      SOFR_AREA,
    ],
    [primaryRateSymbol, secondaryRateSymbol],
  );

  const stablecoinRateChartData = useMemo(() => {
    const rows = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.assetApyHistory || []
      : assetApyGql?.protocolAssetApyHistory || [];
    const byTimestamp = new Map();
    rows.forEach((row) => {
      const timestamp = finiteNumber(row.timestamp);
      if (timestamp <= 0) return;
      const symbol = String(row.symbol || "").toUpperCase();
      const point = byTimestamp.get(timestamp) || { timestamp };
      if (symbol === primaryRateSymbol) {
        point.primaryBorrowApy = finiteNumber(row.borrowApy) * 100;
      }
      if (symbol === secondaryRateSymbol) {
        point.secondaryBorrowApy = finiteNumber(row.borrowApy) * 100;
      }
      if (row.sofrRate !== null && row.sofrRate !== undefined) {
        point.sofrRate = finiteNumber(row.sofrRate) * 100;
      }
      byTimestamp.set(timestamp, point);
    });
    return [...byTimestamp.values()]
      .filter((point) =>
        [
          point.primaryBorrowApy,
          point.secondaryBorrowApy,
        ].some((value) => Number.isFinite(Number(value))),
      )
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [assetApyGql, compoundV3PageGql, isCompoundV3, primaryRateSymbol, secondaryRateSymbol]);
  const visibleStablecoinRateChartData = useMemo(
    () => filterHistoryByWindow(stablecoinRateChartData, rateWindowDays),
    [rateWindowDays, stablecoinRateChartData],
  );

  const balanceChartData = useMemo(() => {
    const raw = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.apyHistory || []
      : apyGql?.protocolApyHistory || [];
    return [...raw]
      .filter(
        (p) =>
          finiteNumber(p.timestamp) > 0 &&
          (Number.isFinite(Number(p.supplyUsd)) ||
            Number.isFinite(Number(p.borrowUsd))),
      )
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [apyGql, compoundV3PageGql, isCompoundV3]);
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
          timestamp: point.timestamp,
          utilizationPct: supplyUsd > 0 ? (borrowUsd / supplyUsd) * 100 : null,
        };
      })
      .filter((point) => Number.isFinite(Number(point.utilizationPct)));
  }, [balanceChartData]);
  const visibleUtilizationChartData = useMemo(
    () => filterHistoryByWindow(utilizationChartData, utilizationWindowDays),
    [utilizationChartData, utilizationWindowDays],
  );

  const collateralItems = useMemo(
    () =>
      groupAssetItems(
        markets.map((m) => ({
          symbol: m.collateralSymbol || m.symbol,
          value: Math.max(0, finiteNumber(m.supplyUsd)),
        })),
      ),
    [markets],
  );
  const debtItems = useMemo(
    () =>
      groupAssetItems(
        markets.map((m) => ({
          symbol: m.symbol,
          value: Math.max(0, finiteNumber(m.borrowUsd)),
        })),
      ),
    [markets],
  );

  const channelFlows = useMemo(
    () => channelFlowsGql?.morphoCuratorFlows || channelFlowsGql?.eulerChannelFlows || [],
    [channelFlowsGql],
  );

  const flowTotals = useMemo(() => {
    if (hasChannelFlows) {
      let netInflow = 0;
      let netOutflow = 0;
      channelFlows.forEach((f) => {
        const val = finiteNumber(f.valueUsd);
        const action = String(f.action || "");
        if (action === "Net Inflow") netInflow += val;
        if (action === "Net Outflow") netOutflow += val;
      });
      return { netInflow, netOutflow, netFlow: netInflow - netOutflow };
    }
    const allFlows = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.alluvialFlows || []
      : flowsGql?.lendingDataPage?.alluvialFlows || [];
    const group = protocolGroupFromKey(protocolKey);
    let netInflow = 0;
    let netOutflow = 0;
    allFlows.forEach((f) => {
      const fGroup = protocolGroupFromKey(f.protocol);
      if (fGroup !== group) return;
      const val = finiteNumber(f.valueUsd);
      const action = String(f.action || "");
      if (action === "Supply Inflow" || action === "Net Inflow") netInflow += val;
      if (action === "Supply Outflow" || action === "Net Outflow") netOutflow += val;
    });
    return { netInflow, netOutflow, netFlow: netInflow - netOutflow };
  }, [compoundV3PageGql, flowsGql, hasChannelFlows, channelFlows, isCompoundV3, protocolKey]);

  const protocolFlows = useMemo(() => {
    const allFlows = isCompoundV3
      ? compoundV3PageGql?.compoundV3ProtocolPage?.alluvialFlows || []
      : flowsGql?.lendingDataPage?.alluvialFlows || [];
    const group = protocolGroupFromKey(protocolKey);
    return allFlows.filter((f) => {
      const fGroup = protocolGroupFromKey(f.protocol);
      return fGroup === group;
    });
  }, [compoundV3PageGql, flowsGql, isCompoundV3, protocolKey]);

  const curatorAllocation = useMemo(() => {
    const rows = curatorAllocationGql?.morphoCuratorAllocationHistory || [];
    const byTimestamp = new Map();
    const totals = new Map();
    rows.forEach((row) => {
      const timestamp = finiteNumber(row.timestamp);
      const curator = row.curator || "Unassigned";
      const suppliedUsd = finiteNumber(row.suppliedUsd);
      if (timestamp <= 0 || suppliedUsd <= 0) return;
      const point = byTimestamp.get(timestamp) || { timestamp };
      point[curator] = (point[curator] || 0) + suppliedUsd;
      byTimestamp.set(timestamp, point);
      totals.set(curator, (totals.get(curator) || 0) + suppliedUsd);
    });
    const curators = [...totals.keys()].sort((a, b) => totals.get(b) - totals.get(a));
    const data = [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);
    return { data, curators };
  }, [curatorAllocationGql]);
  const visibleCuratorAllocationData = useMemo(
    () => filterHistoryByWindow(curatorAllocation.data, curatorAllocationWindowDays),
    [curatorAllocation.data, curatorAllocationWindowDays],
  );

  // --- Sort & Pagination ---
  const handleSort = useCallback((key) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "desc" ? "asc" : "desc"));
        return key;
      }
      setSortDir("desc");
      return key;
    });
    setCurrentPage(1);
  }, [setCurrentPage, setSortDir, setSortKey]);

  const handleTableModeChange = useCallback((mode) => {
    setTableMode(mode);
    setSortKey("supplyUsd");
    setSortDir("desc");
    setCurrentPage(1);
  }, [setCurrentPage, setSortDir, setSortKey, setTableMode]);

  const normalizedTableMode = TABLE_MODES.some((mode) => mode.key === tableMode) ? tableMode : "vaults";
  const activeTableMode = isMorpho ? normalizedTableMode : "markets";
  const tableColumns = isMorpho && activeTableMode === "vaults" ? VAULT_COLUMNS : MARKET_COLUMNS;
  const tableRows = useMemo(() => {
    if (!isMorpho) return markets;
    if (activeTableMode === "vaults") return vaults;
    return markets;
  }, [activeTableMode, isMorpho, markets, vaults]);

  const sortedMarkets = useMemo(() => {
    const mul = sortDir === "desc" ? -1 : 1;
    return tableRows
      .map((market, index) => ({ market, index }))
      .sort((a, b) => {
        const aValue = a.market[sortKey];
        const bValue = b.market[sortKey];
        if (typeof aValue === "string" || typeof bValue === "string") {
          const compared = String(aValue || "").localeCompare(String(bValue || ""));
          return compared === 0 ? a.index - b.index : compared * mul;
        }
        const aNumber = finiteNumber(aValue);
        const bNumber = finiteNumber(bValue);
        if (aNumber === bNumber) return a.index - b.index;
        return (aNumber - bNumber) * mul;
      })
      .map(({ market }) => market);
  }, [tableRows, sortKey, sortDir]);

  const totalPages = Math.ceil(sortedMarkets.length / PAGE_SIZE) || 1;
  const safePage = Math.min(currentPage, totalPages);
  const pagedMarkets = useMemo(
    () => sortedMarkets.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [sortedMarkets, safePage],
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
        <Icon
          size={13}
          className={`shrink-0 transition-opacity ${active ? "opacity-100" : "opacity-35 group-hover:opacity-70"}`}
        />
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

  const renderCuratorAllocationWindowControls = () => (
    <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
      {CURATOR_ALLOCATION_WINDOWS.map((window) => (
        <button
          key={window.label}
          type="button"
          onClick={() => setCuratorAllocationWindowDays(window.days)}
          className={`px-2.5 py-1 text-[10px] uppercase tracking-widest rounded-sm transition-colors ${curatorAllocationWindowDays === window.days
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
    if (row.rowType === "vault" && activeTableMode === "vaults") {
      if (column.key === "supplyUsd") {
        return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.supplyUsd)}</div>;
      }
      if (column.key === "assetSymbol") {
        return (
          <div className="flex items-center justify-center gap-2 text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">
            <img
              src={row.loanIcon}
              alt={row.assetSymbol}
              className="h-5 w-5 rounded-full"
              loading="lazy"
              onError={(e) => {
                e.target.src = `https://ui-avatars.com/api/?name=${row.assetSymbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
              }}
            />
            <span>{row.assetSymbol}</span>
          </div>
        );
      }
      if (column.key === "sharePriceUsd") {
        return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatAssetSharePrice(row.sharePriceAssets, row.assetSymbol)}</div>;
      }
      if (column.key === "supplyApy") {
        return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{row.supplyApy == null ? "-" : formatApy(row.supplyApy)}</div>;
      }
      if (column.key === "exposure") {
        const visibleExposure = row.exposure.slice(0, 5);
        const hiddenCount = Math.max(0, row.exposure.length - visibleExposure.length);
        return (
          <div className="flex items-center justify-center">
            <div className="flex items-center justify-center -space-x-2">
              {visibleExposure.map((item) => (
                <div
                  key={`${row.entityId}-${item.symbol}`}
                  className="h-6 w-6 rounded-full border border-[#050505] bg-[#151515] p-0.5 shadow-sm"
                  title={`${item.symbol} ${formatCurrency(item.valueUsd)}`}
                >
                  <img
                    src={getTokenIcon(item.symbol)}
                    alt={item.symbol}
                    className="h-full w-full rounded-full object-contain"
                    loading="lazy"
                    onError={(e) => {
                      e.target.src = `https://ui-avatars.com/api/?name=${item.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                    }}
                  />
                </div>
              ))}
            </div>
            {hiddenCount > 0 && (
              <span className="ml-2 text-[10px] md:text-[12px] text-gray-400 tracking-widest">
                +{hiddenCount}
              </span>
            )}
            {row.exposure.length === 0 && <span className="text-[10px] md:text-[13px] text-gray-600 tracking-widest">-</span>}
          </div>
        );
      }
      if (column.key === "curator") {
        const curator = row.curator || "Other";
        const curatorIcon = getCuratorIcon(curator, row.curatorAddress);
        return (
          <div className="flex min-w-0 items-center justify-center gap-2 text-center text-[10px] md:text-[13px] text-white tracking-widest">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-white/10 bg-[#151515] p-0.5 shadow-sm">
              <img
                src={curatorIcon}
                alt={curator}
                className="h-full w-full rounded-full object-contain"
                loading="lazy"
                onError={(e) => {
                  e.target.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(curator)}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                }}
              />
            </div>
            <span className="truncate" title={curator}>{curator}</span>
          </div>
        );
      }
    }

    const muted = row.rowType === "vault";
    if (column.key === "netWorth") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.netWorth)}</div>;
    }
    if (column.key === "supplyUsd") {
      return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.supplyUsd)}</div>;
    }
    if (column.key === "borrowUsd") {
      return <div className={`flex justify-center text-center text-[10px] md:text-[13px] tracking-widest ${muted ? "text-gray-600" : "text-white"}`}>{muted ? "-" : formatCurrency(row.borrowUsd)}</div>;
    }
    if (column.key === "supplyApy") {
      return <div className={`flex justify-center text-center text-[10px] md:text-[13px] tracking-widest ${muted ? "text-gray-600" : row.isTrapped ? "text-red-400" : "text-green-500"}`}>{muted ? "-" : formatApy(row.supplyApy)}</div>;
    }
    if (column.key === "borrowApy") {
      return <div className={`flex justify-center text-center text-[10px] md:text-[13px] tracking-widest ${muted ? "text-gray-600" : row.isTrapped ? "text-red-400" : "text-cyan-500"}`}>{muted ? "-" : formatApy(row.borrowApy)}</div>;
    }
    if (column.key === "utilization") {
      return <div className={`flex justify-center text-center text-[10px] md:text-[13px] tracking-widest ${muted ? "text-gray-600" : row.utilization >= 0.995 ? "text-red-400" : "text-gray-300"}`}>{muted ? "-" : formatPercent(row.utilization)}</div>;
    }
    if (column.key === "lltv") {
      return <div className={`flex justify-center text-center text-[10px] md:text-[13px] tracking-widest ${muted ? "text-gray-600" : "text-gray-300"}`}>{muted ? "-" : formatLltvRange(row)}</div>;
    }
    return <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-500 tracking-widest">-</div>;
  };

  const marketsPageLoading = isCompoundV3 ? compoundV3PageLoading : marketsLoading;
  const apyPageLoading = isCompoundV3 ? compoundV3PageLoading : apyLoading;
  const assetApyPageLoading = isCompoundV3 ? compoundV3PageLoading : assetApyLoading;
  const flowsPageLoading = isCompoundV3 ? compoundV3PageLoading : !flowsGql;
  const loading = marketsPageLoading;
  const tableLoading = marketsPageLoading || (isMorpho && activeTableMode !== "markets" && vaultsLoading);
  const tableTitle =
    isMorpho && activeTableMode === "vaults"
      ? `${displayName} Vaults`
      : `${displayName} Markets`;
  const tableCountLabel =
    activeTableMode === "vaults"
      ? `${sortedMarkets.length} vaults`
      : `${sortedMarkets.length} markets`;
  const tableGridClass = activeTableMode === "vaults" ? "grid-cols-8" : "grid-cols-9";
  const tableMinWidthClass = activeTableMode === "vaults" ? "min-w-[980px]" : "min-w-[1120px]";
  const protocolWebsite = PROTOCOL_WEBSITES[protocolKey] || "#";

  // --- Render ---
  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        {/* Breadcrumbs */}
        <div className="flex items-center gap-3 mb-6 transition-all duration-500">
          <span className="font-mono text-[#333] text-[12px]">|—</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button
              onClick={() => navigate("/data")}
              className="hover:text-white transition-colors uppercase"
            >
              data
            </button>
            <span className="text-[#999]">/</span>
            <span className="flex items-center gap-2 text-white">
              <img
                src={getProtocolIcon(protocolKey)}
                alt={displayName}
                className="w-4 h-4 rounded-full"
              />
              {displayName}
            </span>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        {/* Stats Panel — 4-column MetricCell grid */}
        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            {/* Panel 1: Protocol Identity */}
            <MetricCell
              label="OVERVIEW"
              Icon={(props) => (
                <a
                  href={protocolWebsite}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-white transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink {...props} />
                </a>
              )}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col gap-6 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <img
                          src={getProtocolIcon(protocolKey)}
                          alt={displayName}
                          className="w-8 h-8 rounded-full"
                        />
                        <span className="pl-1 text-base md:text-4xl font-light text-white font-mono tracking-tighter uppercase">
                          {displayName}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="TYPE" value="Lending Protocol" />
                  </div>
                </div>
              }
            />
            {/* Panel 2: Assets */}
            <MetricCell
              label="ASSETS"
              Icon={PieChartIcon}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem
                      label="SUPPLIED"
                      value={
                        loading ? "..." : formatCurrency(stats.totalSupply)
                      }
                    />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem
                        label="BORROWED"
                        value={
                          loading ? "..." : formatCurrency(stats.totalBorrow)
                        }
                      />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="LIQUIDITY"
                      value={
                        loading
                          ? "..."
                          : formatCurrency(
                            Math.max(
                              0,
                              stats.totalSupply - stats.totalBorrow,
                            ),
                          )
                      }
                    />
                  </div>
                </div>
              }
            />
            {/* Panel 3: Rates */}
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem
                      label="SUPPLY APR"
                      value={loading ? "..." : formatApy(stats.avgSupplyApy)}
                    />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem
                        label="BORROW APR"
                        value={loading ? "..." : formatApy(stats.avgBorrowApy)}
                      />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="UTILIZATION"
                      value={loading ? "..." : formatPercent(stats.avgUtil)}
                    />
                  </div>
                </div>
              }
            />
            {/* Panel 4: Selected-window flows */}
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
                    <div className={`text-base md:text-xl font-light font-mono tracking-tighter ${flowTotals.netFlow >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}>
                      {formatSignedCurrency(flowTotals.netFlow)}
                    </div>
                  </div>
                </div>
              }
            />
          </div>
        </div>

        {/* 2-Column Chart Grid */}
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          {/* Supply and Borrow Chart */}
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                {displayName} Supply & Borrow
              </h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(balanceWindowDays, setBalanceWindowDays)}
                <div className="flex gap-4">
                  <div className="flex items-center gap-2">
                    <div
                      className="w-2 h-2"
                      style={{ backgroundColor: SUPPLY_USD_AREA.color }}
                    />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                      Supply
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className="w-2 h-2"
                      style={{ backgroundColor: BORROW_USD_AREA.color }}
                    />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                      Borrow
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {apyPageLoading && balanceChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleBalanceChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
                  No balance history available
                </div>
              ) : (
                <RLDPerformanceChart
                  data={visibleBalanceChartData}
                  areas={[SUPPLY_USD_AREA, BORROW_USD_AREA]}
                  resolution={APY_RESOLUTION}
                />
              )}
            </div>
          </div>

          {/* Collateral & Debt Breakdown */}
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                Composition
              </h2>
            </div>
            <div className="flex flex-col flex-1 gap-8 justify-between">
              <div className="flex-1">
                <AssetBreakdownBar
                  items={collateralItems}
                  title="COLLATERAL MIX"
                  loading={marketsPageLoading}
                />
              </div>
              <div className="flex-1">
                <AssetBreakdownBar
                  items={debtItems}
                  title="DEBT MIX"
                  loading={marketsPageLoading}
                />
              </div>
            </div>
          </div>
        </section>

        {/* Alluvial Flow Chart */}
        <section className="mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                {hasChannelFlows ? `${displayName} Funds Flow` : `${displayName} Flows (${activeFlowWindow.label})`}
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
              {hasChannelFlows ? (
                <MorphoCuratorFlowChart
                  flows={channelFlows}
                  loading={channelFlowsLoading}
                  protocolName={displayName}
                  channelLabel="CHANNELS"
                />
              ) : (
                <ProtocolFlowChart
                  flows={protocolFlows}
                  protocolName={displayName}
                  loading={flowsPageLoading}
                />
              )}
            </div>
          </div>
        </section>

        {isMorpho && (
          <section className="mb-6">
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between gap-3 mb-4">
                <div className="flex items-center gap-3">
                  <Activity size={18} className="text-gray-500" />
                  <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                    Curator Market Share
                  </h2>
                </div>
                <div className="flex items-center gap-3 flex-wrap justify-end">
                  {renderCuratorAllocationWindowControls()}
                  <span className="text-[10px] text-gray-600 uppercase tracking-widest">
                    {curatorAllocation.curators.length} Curators
                  </span>
                </div>
              </div>
              <CuratorAllocationChart
                data={visibleCuratorAllocationData}
                curators={curatorAllocation.curators}
                loading={curatorAllocationLoading}
              />
            </div>
          </section>
        )}

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                {primaryRateSymbol}/{secondaryRateSymbol} Interest Rates
              </h2>
              <div className="flex gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(rateWindowDays, setRateWindowDays)}
                <div className="flex items-center gap-2">
                  <div
                    className="w-5 h-px"
                    style={{ backgroundColor: PRIMARY_BORROW_APY_AREA.color }}
                  />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                    {primaryRateSymbol} Borrow
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div
                    className="w-5 h-px"
                    style={{ backgroundColor: SECONDARY_BORROW_APY_AREA.color }}
                  />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                    {secondaryRateSymbol} Borrow
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div
                    className="w-5 h-0 border-t"
                    style={{ borderColor: SOFR_AREA.color, borderTopStyle: "dashed" }}
                  />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                    SOFR
                  </span>
                </div>
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {assetApyPageLoading && stablecoinRateChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleStablecoinRateChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
                  No rate history available
                </div>
              ) : (
                <RLDPerformanceChart
                  data={visibleStablecoinRateChartData}
                  areas={stablecoinBorrowApyAreas}
                  resolution={APY_RESOLUTION}
                />
              )}
            </div>
          </div>
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                Historical Utilization
              </h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                {renderHistoryWindowControls(utilizationWindowDays, setUtilizationWindowDays)}
                <div className="flex items-center gap-2">
                  <div
                    className="w-5 h-px"
                    style={{ backgroundColor: UTILIZATION_AREA.color }}
                  />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">
                    Debt / Collateral
                  </span>
                </div>
              </div>
            </div>
            <div className="h-[300px] w-full relative mt-auto">
              {apyPageLoading && utilizationChartData.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                </div>
              ) : visibleUtilizationChartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
                  No utilization history available
                </div>
              ) : (
                <RLDPerformanceChart
                  data={visibleUtilizationChartData}
                  areas={[UTILIZATION_AREA]}
                  resolution={APY_RESOLUTION}
                  yAxisDomain={[0, "auto"]}
                />
              )}
            </div>
          </div>
        </section>

        {/* Markets Table */}
        <section className="mt-8 border border-white/10 bg-[#080808]">
          <div className="flex flex-col md:flex-row md:items-center justify-between p-4 md:px-6 border-b border-white/10 gap-4">
            <div className="flex items-center gap-3">
              <img
                src={getProtocolIcon(protocolKey)}
                alt={displayName}
                className="w-7 h-7 rounded-full"
              />
              <div>
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  {tableTitle}
                </h2>
                <div className="text-[10px] text-gray-600 uppercase tracking-widest">
                  {tableCountLabel}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4 flex-wrap justify-end">
              {isMorpho && (
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
              )}
              <div className="text-[10px] text-gray-500 uppercase tracking-widest">
                Data provided by <span className="text-white">RLD Protocol</span>
              </div>
            </div>
          </div>

          <div className="w-full overflow-x-auto">
            <div className={`${tableMinWidthClass} flex flex-col`}>
              <div className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]`}>
                <div className="col-span-2">{renderSortHeader("symbol", activeTableMode === "vaults" ? "Name" : "Asset", "justify-start text-left")}</div>
                {tableColumns.map((col) => (
                  <div key={col.key}>{renderSortHeader(col.key, col.label)}</div>
                ))}
              </div>

              <div className="flex flex-col divide-y divide-white/5 relative min-h-[200px]">
                {tableLoading && tableRows.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center mt-12">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : pagedMarkets.length === 0 ? (
                  <div className="flex items-center justify-center py-12 text-xs text-gray-600 uppercase tracking-widest">
                    No {activeTableMode === "vaults" ? "vaults" : "markets"} available
                  </div>
                ) : (
                  pagedMarkets.map((m) => {
                    const isTrapped = m.isTrapped;
                    const isMarketRow = m.rowType !== "vault";
                    const isVaultRow = m.rowType === "vault";
                    const isClickableRow = isMarketRow || (isMorpho && isVaultRow);
                    return (
                      <div
                        key={m.entityId}
                        onClick={() => {
                          if (isMorpho && isVaultRow) {
                            navigate(`/data/morpho/vault/${m.vaultAddress || m.entityId}`);
                            return;
                          }
                          if (!isMarketRow) return;
                          navigate(
                            marketRouteFor(
                              m.protocol || protocolKey,
                              m.entityId,
                            ),
                          );
                        }}
                        className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-4 items-center transition-colors hover:bg-white/[0.02] group ${isClickableRow ? "cursor-pointer" : "cursor-default"} ${isTrapped ? "opacity-50" : ""}`}
                      >
                        <div className="col-span-2 flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm group-hover:border-white/20 transition-colors">
                            <img
                              src={m.collateralIcon || m.loanIcon}
                              alt={m.collateralSymbol || m.symbol}
                              className="w-full h-full object-contain rounded-full"
                              loading="lazy"
                              onError={(e) => {
                                e.target.src = `https://ui-avatars.com/api/?name=${m.collateralSymbol || m.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                              }}
                            />
                          </div>
                          {m.collateralSymbol && (
                            <div className="w-7 h-7 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm group-hover:border-white/20 transition-colors -ml-6 mt-5">
                              <img
                                src={m.loanIcon}
                                alt={m.symbol}
                                className="w-full h-full object-contain rounded-full"
                                loading="lazy"
                                onError={(e) => {
                                  e.target.src = `https://ui-avatars.com/api/?name=${m.symbol}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;
                                }}
                              />
                            </div>
                          )}
                          <div className="flex min-h-10 min-w-0 items-center text-[10px] md:text-[13px] text-white font-bold leading-none pt-1">
                            <span className="truncate" title={m.marketLabel}>{m.marketLabel}</span>
                          </div>
                        </div>
                        {tableColumns.map((col) => (
                          <div key={`${m.entityId}-${col.key}`}>
                            {renderTableCell(m, col)}
                          </div>
                        ))}
                      </div>
                    );
                  })
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
