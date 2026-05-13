import React, { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import {
  Activity,
  ArrowLeft,
  ExternalLink,
  Loader2,
  PieChart as PieChartIcon,
  Shield,
  TrendingUp,
} from "lucide-react";
import {
  Cell,
  Pie,
  PieChart as RechartsPieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { MetricCell, StatItem } from "../../../components/pools/MetricsGrid";
import RLDPerformanceChart from "../../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import { METAMORPHO_VAULT_PAGE_QUERY } from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import { getTokenIcon } from "../../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

const CHART_RESOLUTION = "1D";
const TIMESERIES_LIMIT = 2000;
const FLOW_LIMIT = 2000;
const ALLOCATION_LIMIT = 100;
const HISTORY_WINDOWS = [
  { label: "1M", days: 30 },
  { label: "1Y", days: 365 },
  { label: "ALL", days: null },
];
const PIE_COLORS = [
  "#22d3ee",
  "#34d399",
  "#818cf8",
  "#fb7185",
  "#facc15",
  "#f97316",
  "#a78bfa",
  "#38bdf8",
  "#4ade80",
  "#f472b6",
  "#64748b",
];

const finiteNumber = (value, fallback = 0) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const formatCurrency = (value) => {
  const amount = finiteNumber(value);
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(2)}M`;
  if (amount >= 1e3) return `$${(amount / 1e3).toFixed(0)}K`;
  return `$${amount.toFixed(0)}`;
};

const formatApy = (value) => `${(finiteNumber(value) * 100).toFixed(2)}%`;
const formatPercent = (value, digits = 2) => `${(finiteNumber(value) * 100).toFixed(digits)}%`;
const formatUsdPrice = (value) => {
  const price = finiteNumber(value);
  if (price >= 1000) return formatCurrency(price);
  if (price >= 1) return `$${price.toFixed(4)}`;
  if (price > 0) return `$${price.toPrecision(4)}`;
  return "$0.00";
};
const shortAddress = (value) => {
  const raw = String(value || "");
  return raw.length > 12 ? `${raw.slice(0, 6)}...${raw.slice(-4)}` : raw;
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

const normalizeHistoryPoint = (point) => ({
  timestamp: finiteNumber(point?.timestamp),
  totalDepositsUsd: finiteNumber(point?.totalDepositsUsd),
  allocatedUsd: finiteNumber(point?.allocatedUsd),
  liquidityUsd: finiteNumber(point?.liquidityUsd),
  utilization: finiteNumber(point?.utilization),
  utilizationPct: finiteNumber(point?.utilization) * 100,
  sharePriceUsd: finiteNumber(point?.sharePriceUsd),
  netApy: finiteNumber(point?.netApy),
  netApyPct: finiteNumber(point?.netApy) * 100,
});

const proportionalSlots = (items, totals, top, bottom, height, minHeight = 14, gap = 12) => {
  const slots = new Map();
  if (!items.length) return slots;
  const total = items.reduce((sum, item) => sum + finiteNumber(totals.get(item)), 0);
  const available = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawHeights = items.map((item) => (total > 0 ? (available * finiteNumber(totals.get(item))) / total : available / items.length));
  const heights = rawHeights.map((height) => Math.max(minHeight, height));
  const used = heights.reduce((sum, height) => sum + height, 0) + gap * (items.length - 1);
  const scale = used > available ? available / used : 1;
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
    <div className="h-[320px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500">
      {label}
    </div>
  );
}

function ChartCard({ title, legendItems, controls, loading, empty, emptyLabel, children }) {
  return (
    <div className="border border-white/10 bg-[#0a0a0a] rounded-sm p-6">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Activity size={18} className="text-gray-500" />
          <h2 className="text-sm uppercase tracking-widest text-gray-400 font-bold">{title}</h2>
        </div>
        {(controls || legendItems) && (
          <div className="flex items-center gap-4 flex-wrap justify-end">
            {controls}
            {legendItems?.map(([color, label]) => (
              <div key={label} className="flex items-center gap-2">
                <div className="w-2 h-2" style={{ background: color }} />
                <span className="text-xs text-gray-500 uppercase tracking-widest">{label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {loading ? (
        <div className="h-[320px] w-full flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading...
        </div>
      ) : empty ? (
        <ChartEmptyState label={emptyLabel || "No data available"} />
      ) : (
        <div className="h-[320px] w-full">{children}</div>
      )}
    </div>
  );
}

function HistoryWindowControls({ activeDays, onChange }) {
  return (
    <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
      {HISTORY_WINDOWS.map((window) => (
        <button
          key={window.label}
          type="button"
          onClick={() => onChange(window.days)}
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
}

function ToneStatItem({ label, value, className }) {
  return (
    <div>
      <div className="text-[9px] md:text-sm text-gray-400 uppercase tracking-widest mb-0.5 md:mb-1">
        {label}
      </div>
      <div className={`text-base md:text-xl font-light font-mono tracking-tighter truncate ${className || "text-white"}`}>
        {value}
      </div>
    </div>
  );
}

function MarketIconStack({ collateralSymbol, loanSymbol, size = "md" }) {
  const mainSize = size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const subSize = size === "sm" ? "h-5 w-5" : "h-7 w-7";
  return (
    <div className="flex items-center">
      <div className={`${mainSize} rounded-full bg-[#151515] border border-[#050505] p-0.5`}>
        <img
          src={getTokenIcon(collateralSymbol || loanSymbol)}
          alt={collateralSymbol || loanSymbol}
          className="h-full w-full rounded-full object-contain"
          loading="lazy"
        />
      </div>
      {collateralSymbol && loanSymbol && (
        <div className={`${subSize} -ml-3 mt-4 rounded-full bg-[#151515] border border-[#050505] p-0.5`}>
          <img
            src={getTokenIcon(loanSymbol)}
            alt={loanSymbol}
            className="h-full w-full rounded-full object-contain"
            loading="lazy"
          />
        </div>
      )}
    </div>
  );
}

function ExposureTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const item = payload[0]?.payload;
  if (!item) return null;
  return (
    <div className="rounded-sm border border-white/10 bg-[#0a0a0a] px-3 py-2 text-xs font-mono shadow-2xl">
      <div className="text-white font-bold mb-1">{item.marketLabel}</div>
      <div className="flex justify-between gap-8 text-gray-400">
        <span>Deposits</span>
        <span className="text-white">{formatCurrency(item.suppliedUsd)}</span>
      </div>
      <div className="flex justify-between gap-8 text-gray-400">
        <span>Allocation</span>
        <span className="text-white">{formatPercent(item.allocationShare)}</span>
      </div>
    </div>
  );
}

function ExposurePie({ exposures, totalDeposits }) {
  const displayedExposures = useMemo(() => {
    const allocated = exposures.reduce((sum, item) => sum + finiteNumber(item.suppliedUsd), 0);
    const liquidity = Math.max(0, finiteNumber(totalDeposits) - allocated);
    if (liquidity < Math.max(1, finiteNumber(totalDeposits) * 0.0001)) return exposures;
    return [
      ...exposures,
      {
        marketId: "vault-liquidity",
        marketLabel: "Vault Liquidity",
        suppliedUsd: liquidity,
        allocationShare: finiteNumber(totalDeposits) > 0 ? liquidity / finiteNumber(totalDeposits) : 0,
      },
    ];
  }, [exposures, totalDeposits]);
  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-8 h-full">
      <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>
        <RechartsPieChart>
          <Pie
            data={displayedExposures}
            dataKey="suppliedUsd"
            nameKey="marketLabel"
            innerRadius="58%"
            outerRadius="88%"
            paddingAngle={1}
            stroke="#050505"
            strokeWidth={2}
          >
            {displayedExposures.map((entry, index) => (
              <Cell key={entry.marketId} fill={PIE_COLORS[index % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip content={<ExposureTooltip />} />
        </RechartsPieChart>
      </ResponsiveContainer>
      <div className="flex flex-col justify-center gap-3 min-w-0">
        {displayedExposures.slice(0, 8).map((item, index) => (
          <div key={item.marketId} className="flex items-center justify-between gap-4 text-xs">
            <div className="flex items-center gap-3 min-w-0">
              <span className="h-2.5 w-2.5 shrink-0" style={{ backgroundColor: PIE_COLORS[index % PIE_COLORS.length] }} />
              <span className="text-gray-300 truncate">{item.marketLabel}</span>
            </div>
            <div className="text-right shrink-0">
              <span className="text-white font-bold">{formatPercent(item.allocationShare, 1)}</span>
              <span className="text-gray-500 ml-2">{formatCurrency(item.suppliedUsd)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function VaultFlowAlluvialChart({ links, curatorName }) {
  const [tooltip, setTooltip] = useState(null);
  const inflowTotals = new Map();
  const outflowTotals = new Map();
  links.forEach((link) => {
    const map = link.action === "Net Outflow" ? outflowTotals : inflowTotals;
    map.set(link.asset, (map.get(link.asset) || 0) + finiteNumber(link.valueUsd));
  });
  const inflowAssets = [...inflowTotals.keys()].sort((a, b) => inflowTotals.get(b) - inflowTotals.get(a));
  const outflowAssets = [...outflowTotals.keys()].sort((a, b) => outflowTotals.get(b) - outflowTotals.get(a));
  const totalInflow = inflowAssets.reduce((sum, asset) => sum + finiteNumber(inflowTotals.get(asset)), 0);
  const totalOutflow = outflowAssets.reduce((sum, asset) => sum + finiteNumber(outflowTotals.get(asset)), 0);
  if (!inflowAssets.length && !outflowAssets.length) {
    return <ChartEmptyState label="No vault flow data available" />;
  }

  const width = 1120;
  const height = 360;
  const nodeWidth = 12;
  const xInflow = 190;
  const xCurator = 560;
  const xOutflow = 930;
  const curatorNode = curatorName || "Curator";
  const curatorTotals = new Map([[curatorNode, Math.max(totalInflow, totalOutflow, 1)]]);
  const inflowSlots = proportionalSlots(inflowAssets, inflowTotals, 62, 30, height, 14, 16);
  const outflowSlots = proportionalSlots(outflowAssets, outflowTotals, 62, 30, height, 14, 16);
  const curatorSlots = proportionalSlots([curatorNode], curatorTotals, 110, 90, height, 80, 0);
  const inflowOffsets = new Map();
  const outflowOffsets = new Map();
  const curatorInOffsets = new Map();
  const curatorOutOffsets = new Map();

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
  const linkSeg = (layout, totals, offsets, key, value) => {
    const slot = layout.get(key);
    if (!slot) return null;
    const total = Math.max(1, finiteNumber(totals.get(key)));
    const heightValue = (slot.h * finiteNumber(value)) / total;
    const offset = offsets.get(key) || 0;
    offsets.set(key, offset + heightValue);
    return { y0: slot.y + offset, y1: slot.y + offset + heightValue };
  };
  const showTooltip = (event, link) => {
    const bounds = event.currentTarget.ownerSVGElement.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    setTooltip({ x: x > bounds.width - 220 ? x - 230 : x + 14, y: y + 14, link });
  };
  const flowPaths = [
    ...inflowAssets.map((asset) => {
      const value = finiteNumber(inflowTotals.get(asset));
      const source = linkSeg(inflowSlots, inflowTotals, inflowOffsets, asset, value);
      const target = linkSeg(curatorSlots, curatorTotals, curatorInOffsets, curatorNode, value);
      if (!source || !target) return null;
      return { d: ribbonPath(xInflow + nodeWidth, source, xCurator, target), color: "#34d399", sourceName: asset, targetName: curatorNode, valueUsd: value };
    }),
    ...outflowAssets.map((asset) => {
      const value = finiteNumber(outflowTotals.get(asset));
      const source = linkSeg(curatorSlots, curatorTotals, curatorOutOffsets, curatorNode, value);
      const target = linkSeg(outflowSlots, outflowTotals, outflowOffsets, asset, value);
      if (!source || !target) return null;
      return { d: ribbonPath(xCurator + nodeWidth, source, xOutflow, target), color: "#fb7185", sourceName: curatorNode, targetName: asset, valueUsd: value };
    }),
  ].filter(Boolean);
  const renderNodes = (items, totals, slots, x, anchor, color) => items.map((item) => {
    const slot = slots.get(item);
    if (!slot) return null;
    const labelX = anchor === "end" ? x - 8 : anchor === "middle" ? x + nodeWidth / 2 : x + nodeWidth + 8;
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={color} rx="2" />
        <text
          x={labelX}
          y={anchor === "middle" ? slot.y - 10 : slot.y + slot.h / 2 + 4}
          textAnchor={anchor}
          fill="#f8fafc"
          fontSize="13"
          fontWeight="700"
        >
          {`${item} ${formatCurrency(totals.get(item))}`}
        </text>
      </g>
    );
  });

  return (
    <div className="relative h-full w-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" role="img" aria-label="Vault funds flow">
        <text x={xInflow + nodeWidth / 2} y={24} textAnchor="middle" fill="#64748b" fontSize="11">NET INFLOWS</text>
        <text x={xCurator + nodeWidth / 2} y={24} textAnchor="middle" fill="#64748b" fontSize="11">CURATOR</text>
        <text x={xOutflow + nodeWidth / 2} y={24} textAnchor="middle" fill="#64748b" fontSize="11">NET OUTFLOWS</text>
        {flowPaths.map((link, index) => (
          <path
            key={`${link.sourceName}-${link.targetName}-${index}`}
            d={link.d}
            fill={link.color}
            fillOpacity="0.3"
            onMouseMove={(event) => showTooltip(event, link)}
            onMouseLeave={() => setTooltip(null)}
            className="transition-opacity hover:opacity-80"
          />
        ))}
        {renderNodes(inflowAssets, inflowTotals, inflowSlots, xInflow, "end", "#34d399")}
        {renderNodes([curatorNode], curatorTotals, curatorSlots, xCurator, "middle", "#998EFF")}
        {renderNodes(outflowAssets, outflowTotals, outflowSlots, xOutflow, "start", "#fb7185")}
      </svg>
      {tooltip && (
        <div
          className="absolute z-20 pointer-events-none rounded-sm border border-zinc-800 bg-[#0a0a0a] px-3 py-2 text-xs font-mono shadow-2xl"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="text-white font-bold mb-1">{tooltip.link.sourceName} {"->"} {tooltip.link.targetName}</div>
          <div className="flex justify-between gap-8 text-gray-400">
            <span>Flow</span>
            <span className="text-white">{formatCurrency(tooltip.link.valueUsd)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function MetaMorphoVaultPage() {
  const { vaultAddress } = useParams();
  const navigate = useNavigate();
  const [depositsWindowDays, setDepositsWindowDays] = useState(null);
  const [flowWindowDays, setFlowWindowDays] = useState(null);
  const [apyWindowDays, setApyWindowDays] = useState(null);
  const [utilizationWindowDays, setUtilizationWindowDays] = useState(null);

  const { data: pageGqlData, isLoading: pageLoading } = useSWR(
    queryKeys.apiMetaMorphoVaultPage(API_GRAPHQL_URL, vaultAddress),
    ([, , variables]) =>
      apiGraphQL("MetaMorphoVaultPage", {
        query: METAMORPHO_VAULT_PAGE_QUERY,
        variables: {
          vaultAddress: variables.vaultAddress,
          timeseriesLimit: TIMESERIES_LIMIT,
          flowLimit: FLOW_LIMIT,
          allocationLimit: ALLOCATION_LIMIT,
        },
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const page = pageGqlData?.metamorphoVaultPage || {};
  const vault = page.vault || null;
  const totalDeposits = finiteNumber(vault?.tvlUsd);
  const history = useMemo(
    () => (page.history || [])
      .map(normalizeHistoryPoint)
      .filter((point) => point.timestamp > 0)
      .sort((a, b) => a.timestamp - b.timestamp),
    [page.history],
  );
  const exposures = useMemo(
    () => (page.exposures || [])
      .map((row) => ({
        ...row,
        suppliedUsd: finiteNumber(row.suppliedUsd),
        allocationShare: totalDeposits > 0 ? finiteNumber(row.suppliedUsd) / totalDeposits : finiteNumber(row.allocationShare),
        liquidityUsd: finiteNumber(row.liquidityUsd),
        supplyApy: finiteNumber(row.supplyApy),
        borrowApy: finiteNumber(row.borrowApy),
        utilization: finiteNumber(row.utilization),
      }))
      .filter((row) => row.marketId && row.suppliedUsd > 0)
      .sort((a, b) => b.suppliedUsd - a.suppliedUsd),
    [page.exposures, totalDeposits],
  );
  const flowLinks = page.flowLinks || [];
  const flowData = useMemo(() => {
    const byTimestamp = new Map();
    (page.flowChart || []).forEach((row) => {
      const timestamp = finiteNumber(row.timestamp);
      if (timestamp <= 0) return;
      const point = byTimestamp.get(timestamp) || {
        timestamp,
        inflowUsd: 0,
        outflowUsd: 0,
        netFlowUsd: 0,
      };
      const inflow = Math.max(0, finiteNumber(row.depositUsd));
      const outflow = Math.max(0, finiteNumber(row.withdrawUsd));
      point.inflowUsd += inflow;
      point.outflowUsd -= outflow;
      point.netFlowUsd += finiteNumber(row.netFlowUsd, inflow - outflow);
      byTimestamp.set(timestamp, point);
    });
    return [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);
  }, [page.flowChart]);
  const visibleDepositsHistory = useMemo(
    () => filterHistoryByWindow(history, depositsWindowDays),
    [depositsWindowDays, history],
  );
  const visibleFlowData = useMemo(
    () => filterHistoryByWindow(flowData, flowWindowDays),
    [flowData, flowWindowDays],
  );
  const visibleApyHistory = useMemo(
    () => filterHistoryByWindow(history, apyWindowDays),
    [apyWindowDays, history],
  );
  const visibleUtilizationHistory = useMemo(
    () => filterHistoryByWindow(history, utilizationWindowDays),
    [history, utilizationWindowDays],
  );
  const latestHistory = history[history.length - 1] || null;
  const allocatedUsd = latestHistory?.allocatedUsd ?? exposures.reduce((sum, row) => sum + row.suppliedUsd, 0);
  const liquidityUsd = latestHistory?.liquidityUsd ?? Math.max(0, totalDeposits - allocatedUsd);
  const utilization = latestHistory?.utilization ?? (totalDeposits > 0 ? Math.min(1, allocatedUsd / totalDeposits) : 0);
  const netInflow7d = flowLinks
    .filter((link) => link.action === "Net Inflow")
    .reduce((sum, link) => sum + finiteNumber(link.valueUsd), 0);
  const netOutflow7d = flowLinks
    .filter((link) => link.action === "Net Outflow")
    .reduce((sum, link) => sum + finiteNumber(link.valueUsd), 0);
  const totalNetFlow7d = netInflow7d - netOutflow7d;
  const assetSymbol = vault?.assetSymbol || "UNKNOWN";
  const curatorName = vault?.curator || "Other";
  const vaultName = vault?.name || shortAddress(vaultAddress);

  if (pageLoading && !vault) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-cyan-500" />
      </div>
    );
  }

  if (!vault) {
    return (
      <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-gray-400 font-mono">
        <span className="text-lg">Vault not found or not indexed</span>
        <button onClick={() => navigate("/data/morpho")} className="text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors">
          <ArrowLeft size={16} /> Return to Morpho
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        <div className="flex items-center gap-3 my-6">
          <span className="font-mono text-[#333] text-[12px]">|—</span>
          <div className="flex items-center gap-2 font-mono text-[11px] md:text-[13px] tracking-[0.28em] uppercase text-[#999]">
            <button onClick={() => navigate("/data")} className="hover:text-white transition-colors uppercase">data</button>
            <span>/</span>
            <button onClick={() => navigate("/data/morpho")} className="hover:text-white transition-colors uppercase">MORPHO</button>
            <span>/</span>
            <span className="flex items-center gap-2 text-white">
              <img src={getTokenIcon(assetSymbol)} alt={assetSymbol} className="h-4 w-4 rounded-full grayscale opacity-80" />
              {vaultName}
            </span>
            <a
              href={vaultAddress?.startsWith("0x") ? `https://app.morpho.org/ethereum/vault/${vaultAddress}` : "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={`hover:text-white transition-colors ${!vaultAddress?.startsWith("0x") && "pointer-events-none opacity-40"}`}
            >
              <ExternalLink size={12} />
            </a>
          </div>
          <span className="flex-1 h-px bg-[#141414]" />
        </div>

        <div className="mb-8 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChartIcon}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="flex items-center gap-3">
                    <img src={getTokenIcon(assetSymbol)} alt={assetSymbol} className="h-9 w-9 rounded-full" />
                    <div className="min-w-0">
                      <div className="text-2xl md:text-3xl font-light text-white tracking-tight truncate">{curatorName}</div>
                      <div className="text-xs text-gray-500 truncate">{vaultName}</div>
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="TYPE" value="Curator" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="ASSETS"
              Icon={Shield}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="TOTAL DEPOSITS" value={formatCurrency(totalDeposits)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="LIQUIDITY" value={formatCurrency(liquidityUsd)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="UTILIZATION" value={formatPercent(utilization)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="PERFORMANCE"
              Icon={TrendingUp}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <StatItem label="SHARE PRICE" value={formatUsdPrice(vault.sharePriceUsd)} />
                    <div className="border-l border-white/10 pl-4">
                      <StatItem label="APY" value={formatApy(vault.apy)} />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <StatItem label="ASSET" value={assetSymbol} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="FLOWS 7D"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col gap-4 mt-auto">
                  <div className="grid grid-cols-2 gap-4">
                    <ToneStatItem label="NET INFLOW" value={formatCurrency(netInflow7d)} className="text-green-400" />
                    <div className="border-l border-white/10 pl-4">
                      <ToneStatItem label="NET OUTFLOW" value={formatCurrency(netOutflow7d)} className="text-rose-400" />
                    </div>
                  </div>
                  <div className="border-t border-white/10 pt-3">
                    <ToneStatItem
                      label="TOTAL NET"
                      value={`${totalNetFlow7d >= 0 ? "+" : "-"}${formatCurrency(Math.abs(totalNetFlow7d))}`}
                      className={totalNetFlow7d >= 0 ? "text-green-400" : "text-rose-400"}
                    />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
          <ChartCard
            title="Total Deposits"
            controls={<HistoryWindowControls activeDays={depositsWindowDays} onChange={setDepositsWindowDays} />}
            legendItems={[["#34d399", "Total Deposits"]]}
            loading={pageLoading && !history.length}
            empty={!visibleDepositsHistory.length}
          >
            <RLDPerformanceChart
              data={visibleDepositsHistory}
              resolution={CHART_RESOLUTION}
              areas={[{ key: "totalDepositsUsd", color: "#34d399", name: "Total Deposits", format: "dollar" }]}
            />
          </ChartCard>
          <ChartCard title="Exposure" loading={pageLoading && !exposures.length} empty={!exposures.length} emptyLabel="No market exposure data available">
            <ExposurePie exposures={exposures} totalDeposits={totalDeposits} />
          </ChartCard>
        </div>

        <div className="mb-6">
          <ChartCard
            title="Vault Inflow / Outflow (USD)"
            controls={<HistoryWindowControls activeDays={flowWindowDays} onChange={setFlowWindowDays} />}
            legendItems={[["#34d399", "Inflow"], ["#fb7185", "Outflow"], ["#22d3ee", "Net"]]}
            loading={pageLoading && !flowData.length}
            empty={!visibleFlowData.length}
            emptyLabel="No vault flow history available"
          >
            <RLDPerformanceChart
              data={visibleFlowData}
              resolution={CHART_RESOLUTION}
              referenceLines={[{ y: 0, stroke: "#52525b" }]}
              areas={[
                { key: "inflowUsd", color: "#34d399", name: "Inflow", format: "dollar" },
                { key: "outflowUsd", color: "#fb7185", name: "Outflow", format: "dollar" },
                { key: "netFlowUsd", color: "#22d3ee", name: "Net", format: "dollar", noFill: true },
              ]}
            />
          </ChartCard>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
          <ChartCard
            title="Net APY"
            controls={<HistoryWindowControls activeDays={apyWindowDays} onChange={setApyWindowDays} />}
            legendItems={[["#22d3ee", "Net APY"]]}
            loading={pageLoading && !history.length}
            empty={!visibleApyHistory.length}
          >
            <RLDPerformanceChart
              data={visibleApyHistory}
              resolution={CHART_RESOLUTION}
              areas={[{ key: "netApyPct", color: "#22d3ee", name: "Net APY", format: "percent" }]}
            />
          </ChartCard>
          <ChartCard
            title="Historical Utilization"
            controls={<HistoryWindowControls activeDays={utilizationWindowDays} onChange={setUtilizationWindowDays} />}
            legendItems={[["#a78bfa", "Allocated / Deposits"]]}
            loading={pageLoading && !history.length}
            empty={!visibleUtilizationHistory.length}
          >
            <RLDPerformanceChart
              data={visibleUtilizationHistory}
              resolution={CHART_RESOLUTION}
              areas={[{ key: "utilizationPct", color: "#a78bfa", name: "Utilization", format: "percent" }]}
            />
          </ChartCard>
        </div>

        <section className="border border-white/10 bg-[#080808] rounded-sm overflow-hidden">
          <div className="flex items-center justify-between p-4 md:p-6 border-b border-white/10">
            <div className="flex items-center gap-3">
              <img src={getTokenIcon(assetSymbol)} alt={assetSymbol} className="h-7 w-7 rounded-full" />
              <div>
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">Market Exposures</h2>
                <div className="text-[10px] text-gray-600 uppercase tracking-widest">{exposures.length} markets</div>
              </div>
            </div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest">Data provided by <span className="text-white">RLD Protocol</span></div>
          </div>
          <div className="w-full overflow-x-auto">
            <div className="min-w-[1040px]">
              <div className="grid grid-cols-8 gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]">
                <div className="col-span-2 text-left">Market</div>
                <div className="text-center">Deposits</div>
                <div className="text-center">Allocation</div>
                <div className="text-center">Liquidity</div>
                <div className="text-center">Supply APY</div>
                <div className="text-center">Borrow APY</div>
                <div className="text-center">Utilization</div>
              </div>
              <div className="divide-y divide-white/5 min-h-[160px]">
                {pageLoading && !exposures.length ? (
                  <div className="py-12 flex items-center justify-center text-xs uppercase tracking-widest text-gray-500 gap-2">
                    <Loader2 size={14} className="animate-spin" /> Loading exposures...
                  </div>
                ) : !exposures.length ? (
                  <div className="py-12 text-center text-xs uppercase tracking-widest text-gray-600">No market exposures available</div>
                ) : (
                  exposures.map((row) => (
                    <button
                      key={row.marketId}
                      type="button"
                      onClick={() => navigate(`/data/morpho/${row.marketId}`)}
                      className="w-full grid grid-cols-8 gap-4 px-4 md:px-6 py-4 items-center text-left hover:bg-white/[0.02] transition-colors"
                    >
                      <div className="col-span-2 flex items-center gap-3 min-w-0">
                        <MarketIconStack collateralSymbol={row.collateralSymbol} loanSymbol={row.loanSymbol} size="sm" />
                        <span className="text-[10px] md:text-[13px] text-white font-bold truncate">{row.marketLabel}</span>
                      </div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.suppliedUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{formatPercent(row.allocationShare)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(row.liquidityUsd)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{formatApy(row.supplyApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-cyan-500 tracking-widest">{formatApy(row.borrowApy)}</div>
                      <div className="text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{formatPercent(row.utilization)}</div>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
