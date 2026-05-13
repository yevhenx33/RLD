import React, { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Activity, ArrowLeft, Loader2, ExternalLink, Shield, Link2 } from "lucide-react";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import { PieChart as PieChartIcon } from "lucide-react";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import { MARKET_PAGE_QUERY } from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import { apiProtocolForSlug, normalizeMarketIdForApi } from "../../../lib/protocolConfig";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT_DAYS = 2000;
const FLOW_LIMIT_DAYS = 2000;
const ALLOCATION_LIMIT_DAYS = 2000;

// Top-N vault colors (distinguishable palette)
const VAULT_COLORS = [
  "#22d3ee", "#34d399", "#818cf8", "#fb7185", "#facc15",
  "#f97316", "#a78bfa", "#38bdf8", "#4ade80", "#f472b6",
  "#e879f9", "#fbbf24", "#2dd4bf", "#c084fc", "#fb923c",
];

const finiteNumber = (v, fb = 0) => { const n = Number(v); return Number.isFinite(n) ? n : fb; };
const formatCurrency = (v) => {
  const a = finiteNumber(v);
  if (a >= 1e9) return `$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(a / 1e3).toFixed(0)}K`;
  return `$${a.toFixed(0)}`;
};
const formatApy = (v) => `${(finiteNumber(v) * 100).toFixed(2)}%`;
const formatPercent = (v, d = 2) => `${(finiteNumber(v) * 100).toFixed(d)}%`;
const shortAddress = (value) => {
  const raw = String(value || "");
  if (!raw || /^0x0{40}$/i.test(raw)) return "Unassigned";
  return raw.length > 12 ? `${raw.slice(0, 6)}...${raw.slice(-4)}` : raw;
};
const normalizeRatePoint = (p) => ({
  timestamp: finiteNumber(p?.timestamp),
  supplyApy: finiteNumber(p?.supplyApy),
  borrowApy: finiteNumber(p?.borrowApy),
  utilization: finiteNumber(p?.utilization),
  supplyUsd: finiteNumber(p?.supplyUsd),
  borrowUsd: finiteNumber(p?.borrowUsd),
});
const hasAnyFiniteValue = (p, keys) => keys.some((k) => Number.isFinite(Number(p?.[k])));

const proportionalSlots = (items, totals, top, bottom, height, minH = 14, gap = 12) => {
  const slots = new Map();
  if (!items.length) return slots;
  const total = items.reduce((sum, item) => sum + finiteNumber(totals.get(item)), 0);
  const avail = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawHeights = items.map((item) => (total > 0 ? (avail * finiteNumber(totals.get(item))) / total : avail / items.length));
  const heights = rawHeights.map((heightValue) => Math.max(minH, heightValue));
  const used = heights.reduce((sum, heightValue) => sum + heightValue, 0) + gap * (items.length - 1);
  const scale = used > avail ? avail / used : 1;
  let y = top + Math.max(0, (height - top - bottom - used * scale) / 2);
  items.forEach((item, index) => {
    const h = Math.max(8, heights[index] * scale);
    slots.set(item, { y, h, center: y + h / 2, total: finiteNumber(totals.get(item)) });
    y += h + gap * scale;
  });
  return slots;
};

function ChartEmptyState({ label }) {
  return (
    <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
      {label}
    </div>
  );
}

function ChartCard({ title, legendItems, loading, empty, emptyLabel, children }) {
  return (
    <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Activity size={18} className="text-gray-500" />
          <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">{title}</h2>
        </div>
        {legendItems && (
          <div className="flex items-center gap-4 flex-wrap">
            {legendItems.map(([color, label]) => (
              <div key={label} className="flex items-center gap-2">
                <div className="w-2 h-2" style={{ background: color }} />
                <span className="text-xs text-gray-500 uppercase tracking-widest">{label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {loading ? (
        <div className="h-[300px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading...
        </div>
      ) : empty ? (
        <ChartEmptyState label={emptyLabel || "No data available"} />
      ) : (
        <div className="h-[300px] w-full">{children}</div>
      )}
    </div>
  );
}

// Deserialize columnar allocation data into recharts-compatible pivoted rows
function useAllocationChartData(columnar, genesisTs) {
  return useMemo(() => {
    if (!columnar?.timestamps?.length || !columnar?.vaults?.length) {
      return { pivoted: [], vaultKeys: [], vaultNames: {} };
    }

    const { timestamps, vaults, suppliedUsd } = columnar;
    const vaultKeys = vaults.map((v) => v.address);
    const vaultNames = {};
    for (const v of vaults) {
      vaultNames[v.address] = v.name;
    }

    // Build pivoted rows directly from the matrix — no re-grouping needed
    const pivoted = [];
    const step = Math.max(1, Math.ceil(timestamps.length / 2000));
    for (let ti = 0; ti < timestamps.length; ti += step) {
      const ts = timestamps[ti];
      if (genesisTs > 0 && ts < genesisTs) continue;
      const row = { timestamp: ts };
      for (let vi = 0; vi < vaults.length; vi++) {
        row[vaults[vi].address] = suppliedUsd[vi]?.[ti] || 0;
      }
      pivoted.push(row);
    }

    return { pivoted, vaultKeys, vaultNames };
  }, [columnar, genesisTs]);
}

function useCuratorAlluvialData(columnar, marketLabel) {
  return useMemo(() => {
    if (!columnar?.timestamps?.length || !columnar?.vaults?.length) {
      return { rows: [], curators: [], vaults: [], curatorTotals: new Map(), vaultTotals: new Map(), total: 0, timestamp: 0 };
    }
    const latestIndex = columnar.timestamps.length - 1;
    const rows = columnar.vaults
      .map((vault, index) => {
        const valueUsd = finiteNumber(columnar.suppliedUsd?.[index]?.[latestIndex]);
        const curatorAddress = String(vault.curator || "");
        return {
          market: marketLabel,
          curator: shortAddress(curatorAddress),
          curatorAddress,
          vault: vault.name || shortAddress(vault.address),
          vaultAddress: vault.address,
          valueUsd,
        };
      })
      .filter((row) => row.valueUsd > 0)
      .sort((a, b) => b.valueUsd - a.valueUsd)
      .slice(0, 14);

    const curatorTotals = new Map();
    const vaultTotals = new Map();
    rows.forEach((row) => {
      curatorTotals.set(row.curator, (curatorTotals.get(row.curator) || 0) + row.valueUsd);
      vaultTotals.set(row.vault, (vaultTotals.get(row.vault) || 0) + row.valueUsd);
    });

    return {
      rows,
      curators: [...curatorTotals.keys()].sort((a, b) => curatorTotals.get(b) - curatorTotals.get(a)),
      vaults: [...vaultTotals.keys()].sort((a, b) => vaultTotals.get(b) - vaultTotals.get(a)),
      curatorTotals,
      vaultTotals,
      total: rows.reduce((sum, row) => sum + row.valueUsd, 0),
      timestamp: columnar.timestamps[latestIndex] || 0,
    };
  }, [columnar, marketLabel]);
}

const AllocationTooltip = ({ active, payload, vaultNames }) => {
  if (!active || !payload?.length) return null;
  const ts = payload[0]?.payload?.timestamp;
  const date = ts ? new Date(ts * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "";
  return (
    <div className="bg-[#111] border border-white/10 rounded p-3 text-xs font-mono shadow-xl max-w-[280px]">
      <div className="text-gray-400 mb-2">{date}</div>
      {[...payload].filter(e => e.value > 0).sort((a, b) => b.value - a.value).map((entry) => (
        <div key={entry.dataKey} className="flex justify-between gap-4 py-0.5">
          <span style={{ color: entry.color }} className="truncate max-w-[160px]">
            {vaultNames[entry.dataKey] || entry.dataKey.slice(0, 10)}
          </span>
          <span className="text-white">{formatCurrency(entry.value)}</span>
        </div>
      ))}
    </div>
  );
};

function CuratorAlluvialChart({ model, marketLabel, loading }) {
  const [tooltip, setTooltip] = useState(null);

  if (loading && !model.rows.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
        <Loader2 size={14} className="animate-spin" /> Loading Curator Flows...
      </div>
    );
  }
  if (!model.rows.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
        No curator allocation data available
      </div>
    );
  }

  const width = 1120;
  const height = 360;
  const nodeWidth = 12;
  const xMarket = 150;
  const xCurator = 550;
  const xVault = 930;
  const top = 34;
  const bottom = 24;
  const marketTotals = new Map([[marketLabel, model.total]]);
  const marketSlot = proportionalSlots([marketLabel], marketTotals, top + 70, bottom + 70, height, 52, 0);
  const curatorSlots = proportionalSlots(model.curators, model.curatorTotals, top + 22, bottom, height, 12, 12);
  const vaultSlots = proportionalSlots(model.vaults, model.vaultTotals, top + 22, bottom, height, 10, 10);

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

  const marketOffsets = new Map();
  const curatorInOffsets = new Map();
  const curatorOutOffsets = new Map();
  const vaultOffsets = new Map();
  const curatorLinks = model.curators.map((curator) => {
    const valueUsd = model.curatorTotals.get(curator) || 0;
    const src = linkSeg(marketSlot, marketTotals, marketOffsets, marketLabel, valueUsd);
    const tgt = linkSeg(curatorSlots, model.curatorTotals, curatorInOffsets, curator, valueUsd);
    if (!src || !tgt) return null;
    return {
      d: ribbonPath(xMarket + nodeWidth, src, xCurator, tgt),
      color: "#34d399",
      sourceName: marketLabel,
      targetName: curator,
      valueUsd,
    };
  }).filter(Boolean);
  const vaultLinks = model.rows.map((row) => {
    const src = linkSeg(curatorSlots, model.curatorTotals, curatorOutOffsets, row.curator, row.valueUsd);
    const tgt = linkSeg(vaultSlots, model.vaultTotals, vaultOffsets, row.vault, row.valueUsd);
    if (!src || !tgt) return null;
    return {
      d: ribbonPath(xCurator + nodeWidth, src, xVault, tgt),
      color: "#22d3ee",
      sourceName: row.curator,
      targetName: row.vault,
      valueUsd: row.valueUsd,
    };
  }).filter(Boolean);

  const showTip = (event, link) => {
    const bounds = event.currentTarget.ownerSVGElement.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    setTooltip({ x: x > bounds.width - 180 ? x - 190 : x + 14, y: y + 14, link });
  };
  const renderNodes = (items, totals, slots, x, anchor, color) => items.map((item) => {
    const slot = slots.get(item);
    if (!slot) return null;
    const labelX = anchor === "end" ? x - 8 : anchor === "middle" ? x + nodeWidth / 2 : x + nodeWidth + 8;
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={color} rx="2" />
        <text
          x={labelX}
          y={anchor === "middle" ? slot.y - 8 : slot.y + slot.h / 2 + 4}
          textAnchor={anchor}
          fill="#e5e7eb"
          fontSize="12"
        >
          {anchor === "middle" ? `${item} ${formatCurrency(totals.get(item))}` : `${item} ${formatCurrency(totals.get(item))}`}
        </text>
      </g>
    );
  });

  return (
    <div className="relative w-full h-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full" role="img" aria-label="Morpho curator allocation alluvial chart">
        <g>
          {[...curatorLinks, ...vaultLinks].map((link, index) => (
            <path
              key={index}
              d={link.d}
              fill={link.color}
              fillOpacity="0.28"
              stroke="none"
              onMouseMove={(event) => showTip(event, link)}
              onMouseLeave={() => setTooltip(null)}
              className="transition-opacity hover:opacity-80 cursor-default"
            />
          ))}
          <text x={xMarket + nodeWidth / 2} y={16} textAnchor="middle" fill="#6b7280" fontSize="10">MARKET</text>
          <text x={xCurator + nodeWidth / 2} y={16} textAnchor="middle" fill="#6b7280" fontSize="10">CURATORS</text>
          <text x={xVault + nodeWidth / 2} y={16} textAnchor="middle" fill="#6b7280" fontSize="10">VAULTS</text>
          {renderNodes([marketLabel], marketTotals, marketSlot, xMarket, "middle", "#998EFF")}
          {renderNodes(model.curators, model.curatorTotals, curatorSlots, xCurator, "middle", "#34d399")}
          {renderNodes(model.vaults, model.vaultTotals, vaultSlots, xVault, "start", "#22d3ee")}
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
              <span className="text-gray-500 uppercase tracking-widest text-[10px]">Allocated</span>
              <span className="text-white">{formatCurrency(tooltip.link.valueUsd)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function MorphoMarketPage() {
  const { marketId } = useParams();
  const navigate = useNavigate();
  const protocolSlug = "morpho";
  const protocolKey = apiProtocolForSlug(protocolSlug);
  const normalizedEntityId = normalizeMarketIdForApi(protocolSlug, marketId);

  const { data: pageGqlData, isLoading: pageLoading } = useSWR(
    queryKeys.apiMarketPage(API_GRAPHQL_URL, protocolKey, normalizedEntityId),
    ([, , variables]) =>
      apiGraphQL("MarketPage", {
        query: MARKET_PAGE_QUERY,
        variables: {
          protocol: variables.protocol,
          marketId: variables.marketId,
          timeseriesLimit: TIMESERIES_LIMIT_DAYS,
          flowLimit: FLOW_LIMIT_DAYS,
          allocationLimit: ALLOCATION_LIMIT_DAYS,
        },
      }),
    { refreshInterval: REFRESH_INTERVALS.API_PAGE_MS, dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS, revalidateOnFocus: false }
  );

  const { market, tsData, flowData, allocationColumnar, genesisTs } = useMemo(() => {
    const page = pageGqlData?.marketPage || {};
    const rawMarket = page.market || null;
    let safeMarket = null;
    if (rawMarket) {
      const supplyUsd = Math.max(0, Number(rawMarket.supplyUsd) || 0);
      const borrowUsd = Math.max(0, Number(rawMarket.borrowUsd) || 0);
      safeMarket = {
        symbol: String(rawMarket.symbol || "UNKNOWN"),
        protocol: String(rawMarket.protocol || "MORPHO_MARKET"),
        collateralSymbol: rawMarket.collateralSymbol || null,
        lltv: rawMarket.lltv != null ? Number(rawMarket.lltv) : null,
        collateralUsd: rawMarket.collateralUsd != null ? Number(rawMarket.collateralUsd) : null,
        supplyUsd, borrowUsd,
        supplyApy: Math.max(0, finiteNumber(rawMarket.supplyApy)),
        borrowApy: Math.max(0, finiteNumber(rawMarket.borrowApy)),
        utilization: supplyUsd > 0 ? Math.min(1, borrowUsd / supplyUsd) : 0,
        oracle: rawMarket.oracle || null,
        loanPriceUsd: rawMarket.loanPriceUsd != null ? Number(rawMarket.loanPriceUsd) : null,
        collateralPriceUsd: rawMarket.collateralPriceUsd != null ? Number(rawMarket.collateralPriceUsd) : null,
        loanToken: rawMarket.loanToken || null,
        collateralToken: rawMarket.collateralToken || null,
        oracleSupport: rawMarket.oracleSupport || null,
      };
    }

    const chart = (page.rateChart || [])
      .map(normalizeRatePoint)
      .filter((p) => p.timestamp > 0 && hasAnyFiniteValue(p, ["supplyApy", "borrowApy", "supplyUsd", "borrowUsd", "utilization"]))
      .sort((a, b) => a.timestamp - b.timestamp);

    const flowBase = (page.flowChart || []).map((p) => {
      const supplyOutflowAbs = Math.max(0, finiteNumber(p.supplyOutflowUsd));
      const borrowOutflowAbs = Math.max(0, finiteNumber(p.borrowOutflowUsd));
      return {
        timestamp: finiteNumber(p.timestamp),
        supplyInflowUsd: Math.max(0, finiteNumber(p.supplyInflowUsd)),
        supplyOutflowUsd: -supplyOutflowAbs,
        netSupplyFlowUsd: finiteNumber(p.netSupplyFlowUsd),
        borrowInflowUsd: Math.max(0, finiteNumber(p.borrowInflowUsd)),
        borrowOutflowUsd: -borrowOutflowAbs,
        netBorrowFlowUsd: finiteNumber(p.netBorrowFlowUsd),
        cumulativeSupplyNetInflowUsd: finiteNumber(p.cumulativeSupplyNetInflowUsd, NaN),
        cumulativeBorrowNetInflowUsd: finiteNumber(p.cumulativeBorrowNetInflowUsd, NaN),
      };
    }).filter((p) => p.timestamp > 0).sort((a, b) => a.timestamp - b.timestamp);

    const flow = flowBase.reduce((acc, point) => {
      const hasS = Number.isFinite(point.cumulativeSupplyNetInflowUsd);
      const hasB = Number.isFinite(point.cumulativeBorrowNetInflowUsd);
      const cs = hasS ? point.cumulativeSupplyNetInflowUsd : acc.cumulativeSupply + point.netSupplyFlowUsd;
      const cb = hasB ? point.cumulativeBorrowNetInflowUsd : acc.cumulativeBorrow + point.netBorrowFlowUsd;
      return { cumulativeSupply: cs, cumulativeBorrow: cb, rows: [...acc.rows, { ...point, cumulativeSupplyNetInflowUsd: cs, cumulativeBorrowNetInflowUsd: cb }] };
    }, { cumulativeSupply: 0, cumulativeBorrow: 0, rows: [] }).rows;

    // Derive market "liquidity genesis" — first day cumulative supply inflow > 0.
    // All charts on the page start from this point to avoid long flat-line prefixes.
    const genesisPoint = flow.find((p) => p.cumulativeSupplyNetInflowUsd > 0);
    const genesisTs = genesisPoint ? genesisPoint.timestamp : 0;

    return {
      market: safeMarket,
      tsData: genesisTs > 0 ? chart.filter((p) => p.timestamp >= genesisTs) : chart,
      flowData: genesisTs > 0 ? flow.filter((p) => p.timestamp >= genesisTs) : flow,
      allocationColumnar: page.allocationColumnar || null,
      genesisTs,
    };
  }, [pageGqlData]);

  const { pivoted, vaultKeys, vaultNames } = useAllocationChartData(allocationColumnar, genesisTs);
  const marketLabel = market?.collateralSymbol ? `${market.collateralSymbol}-${market.symbol}` : market?.symbol || "Morpho Market";
  const curatorAlluvial = useCuratorAlluvialData(allocationColumnar, marketLabel);

  if (pageLoading && !market) {
    return (<div className="min-h-screen bg-[#050505] flex items-center justify-center"><Loader2 className="w-8 h-8 text-cyan-500 animate-spin" /></div>);
  }
  if (!market) {
    return (
      <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-gray-400 font-mono">
        <span className="text-lg">Market not found or not indexed</span>
        <button onClick={() => navigate(-1)} className="text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors"><ArrowLeft size={16} /> Return to Hub</button>
      </div>
    );
  }

  const loanSymbol = market.symbol;
  const collateralSymbol = market.collateralSymbol;

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">

        {/* Breadcrumbs */}
        <div className="flex items-center gap-3 my-6">
          <span className="font-mono text-[#333] text-[12px]">|—</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span>/</span>
            <span className="hover:text-white">MORPHO</span>
            <span>/</span>
            <a
              href={normalizedEntityId?.startsWith("0x") ? `https://app.morpho.org/ethereum/market/${normalizedEntityId}` : "#"}
              target="_blank" rel="noopener noreferrer"
              className={`flex items-center gap-2 hover:text-white transition-colors ${!normalizedEntityId?.startsWith("0x") && "pointer-events-none opacity-40"}`}
            >
              {collateralSymbol && (
                <img src={getTokenIcon(collateralSymbol)} alt={collateralSymbol} className="w-4 h-4 rounded-full grayscale opacity-80" />
              )}
              <img src={getTokenIcon(loanSymbol)} alt={loanSymbol} className="w-4 h-4 rounded-full grayscale opacity-80 -ml-1" />
              {collateralSymbol ? `${collateralSymbol} / ${loanSymbol}` : loanSymbol}
              <ExternalLink size={12} className="ml-1 opacity-50" />
            </a>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        {/* Stats Panel — 4-column MetricCell grid */}
        <div className="mb-8 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChartIcon}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SUPPLIED" value={formatCurrency(market.supplyUsd)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROWED" value={formatCurrency(market.borrowUsd)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="LIQUIDITY" value={formatCurrency(Math.max(0, market.supplyUsd - market.borrowUsd))} />
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
                    <StatItem label="SUPPLY APR" value={formatApy(market.supplyApy)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="BORROW APR" value={formatApy(market.borrowApy)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="UTILIZATION" value={formatPercent(market.utilization)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="MARKET_PARAMS"
              Icon={Shield}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-1">COLLATERAL</div>
                      <div className="flex items-center gap-2">
                        {collateralSymbol && (
                          <img src={getTokenIcon(collateralSymbol)} alt={collateralSymbol} className="w-5 h-5 rounded-full" />
                        )}
                        <span className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{collateralSymbol || "—"}</span>
                      </div>
                    </div>
                    <div className="border-l border-white/10 pl-4">
                      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-1">DEBT</div>
                      <div className="flex items-center gap-2">
                        <img src={getTokenIcon(loanSymbol)} alt={loanSymbol} className="w-5 h-5 rounded-full" />
                        <span className="text-base md:text-xl font-light text-white font-mono tracking-tighter">{loanSymbol}</span>
                      </div>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="LLTV" value={market.lltv != null ? formatPercent(market.lltv) : "—"} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="ORACLE"
              Icon={Link2}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <StatItem
                    label="PRICE"
                    value={
                      market.collateralPriceUsd != null
                        ? `$${Number(market.collateralPriceUsd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                        : market.loanPriceUsd != null
                          ? `$${Number(market.loanPriceUsd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                          : "—"
                    }
                  />
                  <div className="border-t border-white/10 pt-3">
                    <StatItem
                      label="PROVIDER"
                      value={
                        market.oracleSupport
                          ? market.oracleSupport.replace(/_/g, " ").replace(/supported/i, "").trim().split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(" ") || "Unknown"
                          : "—"
                      }
                    />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        {/* Curator Allocation Alluvial */}
        <section className="mb-6">
          <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                Curator Allocation Flows
              </h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                <div className="text-[10px] text-gray-500 uppercase tracking-widest">
                  {curatorAlluvial.curators.length} curators
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: "#34d399" }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Market to Curator</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: "#22d3ee" }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Curator to Vault</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="h-[360px] w-full relative mt-auto">
              <CuratorAlluvialChart
                model={curatorAlluvial}
                marketLabel={marketLabel}
                loading={pageLoading}
              />
            </div>
          </div>
        </section>

        {/* Row 1: Interest Rates | Value Locked */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <ChartCard title="Interest Rates" legendItems={[["#34d399", "Supply APY"], ["#22d3ee", "Borrow APY"]]} loading={pageLoading && !tsData.length} empty={!tsData.length} emptyLabel="No rate history">
            <RLDPerformanceChart data={tsData} resolution={CHART_RESOLUTION} areas={[
              { key: "borrowApy", color: "#22d3ee", name: "Borrow APY", format: "percent" },
              { key: "supplyApy", color: "#34d399", name: "Supply APY", format: "percent" },
            ]} />
          </ChartCard>

          <ChartCard title="Value Locked" legendItems={[["#818cf8", "Supply TVL"], ["#fb7185", "Borrow TVL"]]} loading={pageLoading && !tsData.length} empty={!tsData.length} emptyLabel="No value history">
            <RLDPerformanceChart data={tsData} resolution={CHART_RESOLUTION} areas={[
              { key: "supplyUsd", color: "#818cf8", name: "Supply TVL", format: "dollar" },
              { key: "borrowUsd", color: "#fb7185", name: "Borrow TVL", format: "dollar" },
            ]} />
          </ChartCard>
        </div>

        {/* Row 2: Vault Allocations (full width) */}
        <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6 mb-6">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <Activity size={18} className="text-gray-500" />
              <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">Vault Allocations (USD)</h2>
            </div>
            <span className="text-[10px] text-gray-600 uppercase tracking-widest">{vaultKeys.length} vaults</span>
          </div>
          {pageLoading && !pivoted.length ? (
            <div className="h-[360px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
              <Loader2 size={14} className="animate-spin" /> Loading Allocations...
            </div>
          ) : !pivoted.length ? (
            <ChartEmptyState label="No allocation data available" />
          ) : (
            <div className="h-[360px] w-full">
              <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>
                <AreaChart data={pivoted} margin={{ top: 5, right: 40, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" />
                  <XAxis
                    dataKey="timestamp" type="number" scale="time" domain={["dataMin", "dataMax"]}
                    tickFormatter={(v) => new Date(v * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" })}
                    stroke="#71717a" fontSize={12} tickMargin={12} minTickGap={60}
                  />
                  <YAxis
                    tickFormatter={(v) => { if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`; if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`; if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`; return `$${v}`; }}
                    stroke="#71717a" fontSize={12}
                  />
                  <Tooltip content={<AllocationTooltip vaultNames={vaultNames} />} />
                  {vaultKeys.map((addr, i) => (
                    <Area
                      key={addr} type="monotone" dataKey={addr} stackId="1"
                      fill={VAULT_COLORS[i % VAULT_COLORS.length]} fillOpacity={0.7}
                      stroke={VAULT_COLORS[i % VAULT_COLORS.length]} strokeWidth={0}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Row 3: Cumulative Net Inflow (full width) */}
        <div className="mb-6">
          <ChartCard title="Cumulative Net Inflow (USD)" legendItems={[["#60a5fa", "Supply"], ["#bef264", "Borrow"]]} loading={pageLoading && !flowData.length} empty={!flowData.length}>
            <RLDPerformanceChart data={flowData} resolution={CHART_RESOLUTION} referenceLines={[{ y: 0, stroke: "#52525b" }]} areas={[
              { key: "cumulativeSupplyNetInflowUsd", color: "#60a5fa", name: "Cumulative Net Supply Inflow", format: "dollar" },
              { key: "cumulativeBorrowNetInflowUsd", color: "#bef264", name: "Cumulative Net Borrow Inflow", format: "dollar" },
            ]} />
          </ChartCard>
        </div>

        {/* Row 4: Supply Flow | Borrow Flow */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <ChartCard title="Supply Inflow / Outflow (USD)" legendItems={[["#22c55e", "Inflow"], ["#f43f5e", "Outflow"], ["#22d3ee", "Net"]]} loading={pageLoading && !flowData.length} empty={!flowData.length}>
            <RLDPerformanceChart data={flowData} resolution={CHART_RESOLUTION} referenceLines={[{ y: 0, stroke: "#52525b" }]} areas={[
              { key: "supplyInflowUsd", color: "#22c55e", name: "Supply Inflow", format: "dollar" },
              { key: "supplyOutflowUsd", color: "#f43f5e", name: "Supply Outflow", format: "dollar" },
              { key: "netSupplyFlowUsd", color: "#22d3ee", name: "Net Supply Flow", format: "dollar", noFill: true },
            ]} />
          </ChartCard>

          <ChartCard title="Borrow Inflow / Outflow (USD)" legendItems={[["#8b5cf6", "Inflow"], ["#f97316", "Outflow"], ["#facc15", "Net"]]} loading={pageLoading && !flowData.length} empty={!flowData.length}>
            <RLDPerformanceChart data={flowData} resolution={CHART_RESOLUTION} referenceLines={[{ y: 0, stroke: "#52525b" }]} areas={[
              { key: "borrowInflowUsd", color: "#8b5cf6", name: "Borrow Inflow", format: "dollar" },
              { key: "borrowOutflowUsd", color: "#f97316", name: "Borrow Outflow", format: "dollar" },
              { key: "netBorrowFlowUsd", color: "#facc15", name: "Net Borrow Flow", format: "dollar", noFill: true },
            ]} />
          </ChartCard>
        </div>

      </main>
    </div>
  );
}
