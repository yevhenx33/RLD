import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import { MetricCell, StatItem } from "../../components/pools/MetricsGrid";
import { Activity, PieChart as PieChartIcon, Layers, Users, Check, Loader2, ChevronDown, ChevronUp } from "lucide-react";
import RLDPerformanceChart from "../../charts/primitives/RLDPerformanceChart";
import { API_GRAPHQL_URL } from "../../api/endpoints";
import { apiGraphQL } from "../../api/apiClient";
import { LENDING_DATA_QUERY } from "../../api/apiQueries";
import { queryKeys } from "../../api/queryKeys";
import { marketRouteFor, protocolSlugForApiProtocol } from "../../lib/protocolConfig";
import { getProtocolIcon, getTokenIcon } from "../../utils/tokenIcons";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";

const SUPPLY_APY_AREA = {
  key: "averageSupplyApy",
  color: "#34d399",
  name: "Avg Supply APY",
  format: "percent",
  yAxisId: "right",
};
const BORROW_APY_AREA = {
  key: "averageBorrowApy",
  color: "#06b6d4",
  name: "Avg Borrow APY",
  format: "percent",
  yAxisId: "right",
};
const SOFR_AREA = {
  key: "sofrRate",
  color: "#71717a",
  name: "SOFR",
  format: "percent",
  yAxisId: "right",
  noFill: true,
  strokeDasharray: "2 4",
  strokeWidth: 1.5,
};
const FLOW_PROTOCOL_COLORS = {
  Aave: "#998EFF",
  Spark: "#f97316",
  Morpho: "#2973FF",
  Fluid: "#4E80EE",
  Euler: "#23C09B",
  "Compound V3": "#00D395",
};
const NET_FLOW_COLORS = {
  Inflow: "#34d399",
  Outflow: "#fb7185",
};
const FLOW_WINDOWS = [
  { label: "1D", days: 1 },
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
];

const finiteNumber = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

const formatCurrency = (value) => {
  const amount = finiteNumber(value);
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(2)}M`;
  if (amount >= 1e3) return `$${(amount / 1e3).toFixed(0)}K`;
  return `$${amount.toFixed(0)}`;
};

const formatSignedCurrency = (value) => {
  const amount = finiteNumber(value);
  if (Math.abs(amount) < 1) return "$0";
  return `${amount < 0 ? "-" : "+"}${formatCurrency(Math.abs(amount))}`;
};

const formatApy = (value) => {
  return `${(finiteNumber(value) * 100).toFixed(2)}%`;
};

const formatPercent = (value, digits = 1) => {
  return `${(finiteNumber(value) * 100).toFixed(digits)}%`;
};

const formatCount = (value) => {
  const count = finiteNumber(value);
  if (count >= 1e6) return `${(count / 1e6).toFixed(2)}M`;
  if (count >= 1e3) return `${(count / 1e3).toFixed(1)}K`;
  return `${Math.round(count)}`;
};

const sumBy = (rows, key) => {
  const totals = new Map();
  rows.forEach((row) => {
    const id = row[key] || "UNKNOWN";
    totals.set(id, (totals.get(id) || 0) + finiteNumber(row.valueUsd));
  });
  return totals;
};

const displayProtocolName = (protocol) => {
  const group = protocolGroup(protocol);
  if (group === "AAVE") return "Aave";
  if (group === "SPARK") return "Spark";
  if (group === "MORPHO") return "Morpho";
  if (group === "EULER") return "Euler";
  if (group === "FLUID") return "Fluid";
  if (group === "COMPOUND_V3") return "Compound V3";
  return protocol || "UNKNOWN";
};

const flowDirection = (action) => {
  if (action === "Supply Inflow" || action === "Borrow Outflow" || action === "Net Inflow") return "Inflow";
  if (action === "Supply Outflow" || action === "Borrow Inflow" || action === "Net Outflow") return "Outflow";
  return null;
};

const aggregateAlluvialFlows = (flows) => {
  const assetTotals = sumBy(flows, "asset");
  const topAssets = new Set(
    [...assetTotals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12)
      .map(([asset]) => asset)
  );
  const grouped = new Map();
  flows.forEach((flow) => {
    const protocol = displayProtocolName(flow.protocol);
    const direction = flowDirection(flow.action);
    if (!direction) return;
    const asset = topAssets.has(flow.asset) ? flow.asset : "Other";
    const key = `${protocol}|${asset}`;
    const current = grouped.get(key) || { protocol, asset, inflowUsd: 0, outflowUsd: 0 };
    const value = finiteNumber(flow.valueUsd);
    if (direction === "Inflow") {
      current.inflowUsd += value;
    } else {
      current.outflowUsd += value;
    }
    grouped.set(key, current);
  });
  return [...grouped.values()].flatMap((row) => {
    const rows = [];
    if (row.inflowUsd > 0) rows.push({
      protocol: row.protocol,
      asset: row.asset,
      direction: "Inflow",
      valueUsd: row.inflowUsd,
    });
    if (row.outflowUsd > 0) rows.push({
      protocol: row.protocol,
      asset: row.asset,
      direction: "Outflow",
      valueUsd: row.outflowUsd,
    });
    return rows;
  });
};

const proportionalSlots = (items, totals, top, bottom, height, minNodeHeight = 18, gap = 38) => {
  const slots = new Map();
  if (!items.length) return slots;
  const total = items.reduce((sum, item) => sum + finiteNumber(totals.get(item)), 0);
  const available = Math.max(1, height - top - bottom - gap * (items.length - 1));
  const rawHeights = items.map((item) => {
    if (total <= 0) return available / items.length;
    return (available * finiteNumber(totals.get(item))) / total;
  });
  const visibleHeights = rawHeights.map((slotHeight) => Math.max(minNodeHeight, slotHeight));
  const visibleTotal = visibleHeights.reduce((sum, slotHeight) => sum + slotHeight, 0);
  const scale = visibleTotal > available ? available / visibleTotal : 1;
  const heights = visibleHeights.map((slotHeight) => Math.max(10, slotHeight * scale));
  const used = heights.reduce((sum, slotHeight) => sum + slotHeight, 0) + gap * (items.length - 1);
  let y = top + Math.max(0, (height - top - bottom - used) / 2);
  items.forEach((item, index) => {
    const h = heights[index];
    slots.set(item, {
      y,
      h,
      center: y + h / 2,
      total: totals.get(item) || 0,
    });
    y += h + gap;
  });
  return slots;
};

const sortFlowItems = (totals) => (a, b) => {
  if (a === "Other" && b !== "Other") return 1;
  if (b === "Other" && a !== "Other") return -1;
  return totals.get(b) - totals.get(a);
};

const AlluvialFlowChart = ({ flows = [], protocolStats = [], loading = false }) => {
  const [flowTooltip, setFlowTooltip] = useState(null);
  const model = useMemo(() => {
    const rows = aggregateAlluvialFlows(flows);
    const protocolFlowTotals = sumBy(rows, "protocol");
    const protocolNetTotals = new Map();
    rows.forEach((row) => {
      const signedValue = row.direction === "Inflow" ? row.valueUsd : -row.valueUsd;
      protocolNetTotals.set(row.protocol, (protocolNetTotals.get(row.protocol) || 0) + signedValue);
    });
    const inflowRows = rows.filter((row) => row.direction === "Inflow");
    const outflowRows = rows.filter((row) => row.direction === "Outflow");
    const protocolInflowTotals = sumBy(inflowRows, "protocol");
    const protocolOutflowTotals = sumBy(outflowRows, "protocol");
    const inflowAssetTotals = sumBy(inflowRows, "asset");
    const outflowAssetTotals = sumBy(outflowRows, "asset");

    const protocolSupplyTotals = new Map();
    protocolStats.forEach(row => {
      protocolSupplyTotals.set(displayProtocolName(row.protocol), row.supplyUsd);
    });

    const protocols = [...protocolFlowTotals.keys()].sort((a, b) => {
      const supplyA = protocolSupplyTotals.get(a) || 0;
      const supplyB = protocolSupplyTotals.get(b) || 0;
      return supplyB - supplyA;
    });
    const inflowAssets = [...inflowAssetTotals.keys()].sort(sortFlowItems(inflowAssetTotals));
    const outflowAssets = [...outflowAssetTotals.keys()].sort(sortFlowItems(outflowAssetTotals));
    const total = [...protocolFlowTotals.values()].reduce((sum, value) => sum + value, 0);
    const maxLinkValue = rows.reduce((max, row) => Math.max(max, row.valueUsd), 0);
    return {
      rows,
      inflowRows,
      outflowRows,
      protocolFlowTotals,
      protocolInflowTotals,
      protocolOutflowTotals,
      protocolNetTotals,
      protocolSupplyTotals,
      inflowAssetTotals,
      outflowAssetTotals,
      protocols,
      inflowAssets,
      outflowAssets,
      total,
      maxLinkValue,
    };
  }, [flows, protocolStats]);

  if (loading && flows.length === 0) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
      </div>
    );
  }
  if (!model.rows.length) {
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
  const xInflowAsset = 165;
  const xProtocol = 555;
  const xOutflowAsset = 930;

  const inflowAssetLayout = proportionalSlots(model.inflowAssets, model.inflowAssetTotals, top + 22, bottom + 8, height, 10, 9);
  const protocolLayout = proportionalSlots(model.protocols, model.protocolSupplyTotals, top + 64, bottom + 38, height, 14, 42);
  const outflowAssetLayout = proportionalSlots(model.outflowAssets, model.outflowAssetTotals, top + 22, bottom + 8, height, 10, 9);
  const headerY = 14;
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
    return {
      y0: slot.y + offset,
      y1: slot.y + offset + dy,
    };
  };

  const inflowAssetOffsets = new Map();
  const outflowAssetOffsets = new Map();
  const protocolInflowOffsets = new Map();
  const protocolOutflowOffsets = new Map();
  const showFlowTooltip = (event, link) => {
    const bounds = event.currentTarget.ownerSVGElement.getBoundingClientRect();
    setFlowTooltip({
      x: event.clientX - bounds.left + 12,
      y: event.clientY - bounds.top + 12,
      title: link.title,
      color: link.color,
    });
  };
  const inflowLinks = model.inflowRows.sort((a, b) => b.valueUsd - a.valueUsd).map((row) => {
    const source = linkSegment(inflowAssetLayout, model.inflowAssetTotals, inflowAssetOffsets, row.asset, row.valueUsd);
    const target = linkSegment(protocolLayout, model.protocolInflowTotals, protocolInflowOffsets, row.protocol, row.valueUsd);
    if (!source || !target) return null;
    return {
      d: ribbonPath(xInflowAsset + nodeWidth, source, xProtocol, target),
      color: NET_FLOW_COLORS.Inflow,
      title: `${row.asset} -> ${row.protocol}: ${formatCurrency(row.valueUsd)}`,
    };
  }).filter(Boolean);

  const outflowLinks = model.outflowRows.sort((a, b) => b.valueUsd - a.valueUsd).map((row) => {
    const source = linkSegment(protocolLayout, model.protocolOutflowTotals, protocolOutflowOffsets, row.protocol, row.valueUsd);
    const target = linkSegment(outflowAssetLayout, model.outflowAssetTotals, outflowAssetOffsets, row.asset, row.valueUsd);
    if (!source || !target) return null;
    return {
      d: ribbonPath(xProtocol + nodeWidth, source, xOutflowAsset, target),
      color: NET_FLOW_COLORS.Outflow,
      title: `${row.protocol} -> ${row.asset}: ${formatCurrency(row.valueUsd)}`,
    };
  }).filter(Boolean);

  const renderNodes = (items, totals, slots, x, anchor, fallbackColor = "#64748b") => items.map((item) => {
    const slot = slots.get(item);
    const color = FLOW_PROTOCOL_COLORS[item] || fallbackColor;
    const labelX = anchor === "end" ? x - 8 : x + nodeWidth + 8;
    const textAnchor = anchor === "middle" ? "middle" : anchor;
    const centeredX = anchor === "middle" ? x + nodeWidth / 2 : labelX;
    const isProtocolNode = anchor === "middle";
    return (
      <g key={item}>
        <rect x={x} y={slot.y} width={nodeWidth} height={slot.h} fill={color} rx="2" />
        {isProtocolNode ? (
          <text x={centeredX} y={slot.y - 8} textAnchor="middle" fill="#e5e7eb" fontSize="12">
            {`${item}  ${formatSignedCurrency(model.protocolNetTotals.get(item))}`}
          </text>
        ) : (
          <text x={centeredX} y={slot.y + slot.h / 2 + 4} textAnchor={textAnchor} fill="#e5e7eb" fontSize="12">
            {`${item} ${formatCurrency(totals.get(item))}`}
          </text>
        )}
      </g>
    );
  });

  return (
    <div className="relative w-full h-full">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-full"
        role="img"
        aria-label="Selected window lending flow alluvial chart"
      >
        <g>
          {[...inflowLinks, ...outflowLinks].map((link, index) => (
            <path
              key={index}
              d={link.d}
              fill={link.color}
              fillOpacity="0.32"
              stroke="none"
              onMouseMove={(event) => showFlowTooltip(event, link)}
              onMouseLeave={() => setFlowTooltip(null)}
              className="transition-opacity hover:opacity-80 cursor-default"
            />
          ))}
          <text x={xInflowAsset + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET INFLOWS</text>
          <text x={xProtocol + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">PROTOCOL</text>
          <text x={xOutflowAsset + nodeWidth / 2} y={headerY} textAnchor="middle" fill="#6b7280" fontSize="10">NET OUTFLOWS</text>
          {renderNodes(model.inflowAssets, model.inflowAssetTotals, inflowAssetLayout, xInflowAsset, "end", NET_FLOW_COLORS.Inflow)}
          {renderNodes(model.protocols, model.protocolFlowTotals, protocolLayout, xProtocol, "middle")}
          {renderNodes(model.outflowAssets, model.outflowAssetTotals, outflowAssetLayout, xOutflowAsset, "start", NET_FLOW_COLORS.Outflow)}
        </g>
      </svg>
      {flowTooltip && (
        <div
          className="absolute z-20 pointer-events-none rounded-sm border border-zinc-800 bg-[#0a0a0a] px-3 py-2 text-xs font-mono text-zinc-200 shadow-2xl"
          style={{ left: flowTooltip.x, top: flowTooltip.y }}
        >
          <div className="flex items-center gap-2 whitespace-nowrap">
            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: flowTooltip.color }} />
            <span>{flowTooltip.title}</span>
          </div>
        </div>
      )}
    </div>
  );
};

const protocolGroup = (protocol) => {
  const normalized = String(protocol || "").toUpperCase();
  if (normalized === "AAVE" || normalized === "AAVE_MARKET") return "AAVE";
  if (normalized === "SPARK" || normalized === "SPARK_MARKET") return "SPARK";
  if (normalized === "MORPHO" || normalized === "MORPHO_MARKET") return "MORPHO";
  if (normalized === "FLUID" || normalized === "FLUID_MARKET") return "FLUID";
  if (normalized === "EULER" || normalized === "EULER_MARKET") return "EULER";
  if (normalized === "COMPOUND_V3" || normalized === "COMPOUND_V3_MARKET" || normalized === "COMPOUND V3") return "COMPOUND_V3";
  if (normalized.startsWith("PENDLE")) return "PENDLE";
  return normalized;
};

const protocolLabel = (protocol) => {
  const group = protocolGroup(protocol);
  if (group === "AAVE") return "AAVE_V3";
  if (group === "SPARK") return "SPARK";
  if (group === "MORPHO") return "MORPHO";
  if (group === "FLUID") return "FLUID";
  if (group === "EULER") return "EULER";
  if (group === "COMPOUND_V3") return "COMPOUND V3";
  return group || "UNKNOWN";
};

const LENDING_PROTOCOL_GROUPS = ["AAVE", "SPARK", "MORPHO", "FLUID", "EULER", "COMPOUND_V3"];
const API_PROTOCOL_BY_GROUP = {
  AAVE: "AAVE_MARKET",
  SPARK: "SPARK_MARKET",
  MORPHO: "MORPHO_MARKET",
  FLUID: "FLUID_MARKET",
  EULER: "EULER_MARKET",
  COMPOUND_V3: "COMPOUND_V3_MARKET",
};

const PROTOCOL_FILTER_LABELS = {
  COMPOUND_V3: "COMPOUND V3",
};

const aggregateLendingProtocols = (markets) => {
  const groups = new Map(
    LENDING_PROTOCOL_GROUPS.map((group) => [
      group,
      {
        group,
        protocol: API_PROTOCOL_BY_GROUP[group],
        marketCount: 0,
        netWorth: 0,
        supplyUsd: 0,
        borrowUsd: 0,
        supplyApyWeighted: 0,
        supplyWeight: 0,
        borrowApyWeighted: 0,
        borrowWeight: 0,
      },
    ]),
  );

  markets.forEach((market) => {
    const group = protocolGroup(market.protocol);
    const row = groups.get(group);
    if (!row) return;

    const supplyUsd = Math.max(0, finiteNumber(market.supplyUsd));
    const borrowUsd = Math.max(0, finiteNumber(market.borrowUsd));
    const netWorth = supplyUsd - borrowUsd;
    const supplyApy = Math.max(0, finiteNumber(market.supplyApy));
    const borrowApy = Math.max(0, finiteNumber(market.borrowApy));

    row.marketCount += 1;
    row.supplyUsd += supplyUsd;
    row.borrowUsd += borrowUsd;
    row.netWorth += netWorth;
    row.supplyApyWeighted += supplyApy * supplyUsd;
    row.supplyWeight += supplyUsd;
    row.borrowApyWeighted += borrowApy * borrowUsd;
    row.borrowWeight += borrowUsd;
  });

  return [...groups.values()]
    .filter((row) => row.marketCount > 0)
    .map((row) => ({
      isProtocolAggregate: true,
      entityId: null,
      symbol: protocolLabel(row.protocol),
      protocol: row.protocol,
      marketCount: row.marketCount,
      netWorth: row.netWorth,
      supplyUsd: row.supplyUsd,
      borrowUsd: row.borrowUsd,
      supplyApy: row.supplyWeight > 0 ? row.supplyApyWeighted / row.supplyWeight : 0,
      borrowApy: row.borrowWeight > 0 ? row.borrowApyWeighted / row.borrowWeight : 0,
      utilization: row.supplyUsd > 0 ? row.borrowUsd / row.supplyUsd : 0,
    }))
    .sort((a, b) => b.supplyUsd - a.supplyUsd);
};

const tableSortValue = (row, key) => {
  if (key === "name") {
    if (row.isProtocolAggregate) return protocolLabel(row.protocol);
    if (row.collateralSymbol && protocolGroup(row.protocol) === "MORPHO") {
      return `${row.collateralSymbol} / ${row.symbol}`;
    }
    return row.symbol || "";
  }
  if (key === "protocol") return protocolLabel(row.protocol);
  return finiteNumber(row[key]);
};

const CustomCheckbox = ({ label, checked = false, disabled = false, onClick }) => (
  <button
    type="button"
    onClick={disabled ? undefined : onClick}
    className={`w-full text-left flex items-center gap-3 select-none ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:opacity-80 transition-opacity"
      }`}
  >
    <div className={`w-4 h-4 rounded-sm border flex items-center justify-center transition-colors ${checked ? 'bg-cyan-500 border-cyan-500' : 'bg-[#080808] border-white/20'
      }`}>
      {checked && <Check size={12} strokeWidth={3} className="text-black" />}
    </div>
    <span className="text-xs tracking-wide">{label}</span>
  </button>
);

export default function LendingDataPage() {
  const navigate = useNavigate();
  const [displayUnit] = useState("USD");
  const [currentPage, setCurrentPage] = useState(1);
  const [protocolFilter, setProtocolFilter] = useState('LENDING');
  const [sortConfig, setSortConfig] = useState({ key: "supplyUsd", direction: "desc" });
  const [flowWindowDays, setFlowWindowDays] = useState(30);
  const activeFlowWindow = FLOW_WINDOWS.find((window) => window.days === flowWindowDays) || FLOW_WINDOWS[2];
  const { data: gqlData, error: _error, isLoading: loading } = useSWR(
    queryKeys.apiLendingPage(API_GRAPHQL_URL, displayUnit, flowWindowDays),
    ([, , variables]) =>
      apiGraphQL("LendingDataHub", {
        query: LENDING_DATA_QUERY,
        variables,
      }),
    { refreshInterval: REFRESH_INTERVALS.API_PAGE_MS, dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS }
  );

  const { stats, chartData, alluvialFlows, marketsData } = useMemo(() => {
    const page = gqlData?.lendingDataPage || {};
    return {
      stats: page.stats || {
        totalSupplyUsd: 0,
        totalBorrowUsd: 0,
        pooledSupplyUsd: 0,
        isolatedSupplyUsd: 0,
        averageSupplyApy: 0,
        averageBorrowApy: 0,
        marketCount: 0,
        totalUsers: 0,
      },
      chartData: page.chartData || [],
      alluvialFlows: page.alluvialFlows || [],
      marketsData: page.markets || [],
    };
  }, [gqlData]);

  const handleProtocolFilter = (filter) => {
    setProtocolFilter(filter);
    setSortConfig((prev) => (
      filter === 'LENDING' && prev.key === "protocol"
        ? { key: "supplyUsd", direction: "desc" }
        : prev
    ));
    setCurrentPage(1);
  };

  const handleSort = (key) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === "desc" ? "asc" : "desc",
    }));
    setCurrentPage(1);
  };

  const lendingProtocolRows = useMemo(() => {
    return aggregateLendingProtocols(marketsData);
  }, [marketsData]);

  const tableRows = useMemo(() => {
    if (protocolFilter === 'LENDING') return lendingProtocolRows;
    return marketsData.filter(pool => {
      const protocol = protocolGroup(pool.protocol);
      if (protocol === 'PENDLE') return false;
      if (protocolFilter === 'ALL') return true;
      return protocol === protocolFilter;
    });
  }, [lendingProtocolRows, marketsData, protocolFilter]);

  const sortedTableRows = useMemo(() => {
    const direction = sortConfig.direction === "asc" ? 1 : -1;
    return tableRows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => {
        const aValue = tableSortValue(a.row, sortConfig.key);
        const bValue = tableSortValue(b.row, sortConfig.key);
        if (typeof aValue === "string" || typeof bValue === "string") {
          const compared = String(aValue).localeCompare(String(bValue));
          return compared === 0 ? a.index - b.index : compared * direction;
        }
        if (aValue === bValue) return a.index - b.index;
        return (aValue - bValue) * direction;
      })
      .map(({ row }) => row);
  }, [sortConfig, tableRows]);

  const ITEMS_PER_PAGE = 10;
  const maxPage = Math.ceil(sortedTableRows.length / ITEMS_PER_PAGE) || 1;
  const safeCurrentPage = Math.min(currentPage, maxPage);

  const paginatedMarkets = useMemo(() => {
    const startIndex = (safeCurrentPage - 1) * ITEMS_PER_PAGE;
    return sortedTableRows.slice(startIndex, startIndex + ITEMS_PER_PAGE);
  }, [safeCurrentPage, sortedTableRows]);

  const totalPages = maxPage;
  const showProtocolColumn = protocolFilter !== 'LENDING';
  const tableGridClass = showProtocolColumn ? "grid-cols-9" : "grid-cols-8";

  const renderSortHeader = (key, label, className = "text-center justify-center") => {
    const active = sortConfig.key === key;
    const Icon = active && sortConfig.direction === "asc" ? ChevronUp : ChevronDown;
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

  const interestRateChartData = useMemo(() => {
    return chartData.filter((point) => {
      const hasSupplyApy =
        point.averageSupplyApy !== null &&
        point.averageSupplyApy !== undefined &&
        Number.isFinite(Number(point.averageSupplyApy));
      const hasBorrowApy =
        point.averageBorrowApy !== null &&
        point.averageBorrowApy !== undefined &&
        Number.isFinite(Number(point.averageBorrowApy));
      return (
        hasSupplyApy ||
        hasBorrowApy
      );
    });
  }, [chartData]);

  const tvlArea = useMemo(() => {
    if (displayUnit === "USD") {
      return {
        key: "tvl",
        color: "#22d3ee",
        name: "Total TVL",
        format: "dollar",
      };
    }
    return {
      key: "tvl",
      color: "#22d3ee",
      name: `Total TVL (${displayUnit})`,
      format: "asset",
      unit: displayUnit,
    };
  }, [displayUnit]);

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">


        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChartIcon}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="TOTAL NET WORTH" value={formatCurrency(Math.max(0, finiteNumber(stats.totalSupplyUsd) - finiteNumber(stats.totalBorrowUsd)))} />
                  </div>
                  <div className="flex flex-col justify-center gap-2 border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="TOTAL SUPPLY" value={formatCurrency(stats.totalSupplyUsd)} />
                    <StatItem label="TOTAL BORROW" value={formatCurrency(stats.totalBorrowUsd)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="AVG SUPPLY" value={formatApy(stats.averageSupplyApy)} />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="AVG BORROW" value={formatApy(stats.averageBorrowApy)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="TVL BY TYPE"
              Icon={Layers}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="POOLED" value={formatCurrency(stats.pooledSupplyUsd)} />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="ISOLATED" value={formatCurrency(stats.isolatedSupplyUsd)} />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="STATS"
              Icon={Users}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end gap-2">
                    <StatItem label="PROTOCOLS" value={lendingProtocolRows.length} />
                    <StatItem label="MARKETS" value={stats.marketCount} />
                  </div>
                  <div className="flex flex-col justify-end gap-2 border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="USERS (1M)" value="-" />
                    <StatItem label="USERS (1Y)" value={formatCount(stats.totalUsers)} />
                  </div>
                </div>
              }
            />
          </div>
        </div>

        <section className="mt-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

            {/* Top-Left: Historical Interest Rates */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  WEIGHTED USDC INTEREST RATES
                </h2>
                <div className="flex gap-4">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: SUPPLY_APY_AREA.color }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Supply</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: BORROW_APY_AREA.color }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">Borrow</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2" style={{ backgroundColor: SOFR_AREA.color }} />
                    <span className="text-[9px] text-gray-500 uppercase tracking-widest">SOFR</span>
                  </div>
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto">
                {loading && interestRateChartData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={interestRateChartData}
                    areas={[SUPPLY_APY_AREA, BORROW_APY_AREA, SOFR_AREA]}
                    resolution="1D"
                  />
                )}
              </div>
            </div>

            {/* Top-Right: Historical Protocol TVL */}
            <div className="flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                  {`TOTAL TVL (${displayUnit})`}
                </h2>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2" style={{ backgroundColor: tvlArea.color }} />
                  <span className="text-[9px] text-gray-500 uppercase tracking-widest">USD</span>
                </div>
              </div>
              <div className="h-[280px] w-full relative mt-auto">
                {loading && chartData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    areas={[tvlArea]}
                    resolution="1D"
                  />
                )}
              </div>
            </div>

          </div>

          <div className="mt-6 flex flex-col p-4 md:p-6 border border-white/10 bg-[#080808] rounded-sm">
            <div className="flex items-center justify-between mb-4 gap-4">
              <h2 className="text-sm md:text-lg text-white font-semibold tracking-tight uppercase">
                {`NET ${activeFlowWindow.label} LENDING FLOWS`}
              </h2>
              <div className="flex items-center gap-4 flex-wrap justify-end">
                <div className="flex items-center gap-1 border border-white/10 bg-[#050505] p-1 rounded-sm">
                  {FLOW_WINDOWS.map((window) => (
                    <button
                      key={window.label}
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
                  {Object.entries(NET_FLOW_COLORS).map(([label, color]) => (
                    <div key={label} className="flex items-center gap-2">
                      <div className="w-2 h-2" style={{ backgroundColor: color }} />
                      <span className="text-[9px] text-gray-500 uppercase tracking-widest">{`NET_${label}`}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="h-[390px] w-full relative mt-auto">
              <AlluvialFlowChart flows={alluvialFlows} protocolStats={lendingProtocolRows} loading={loading} />
            </div>
          </div>
        </section>

        {/* Markets Table Section */}
        <section className="mt-8 border border-white/10 bg-[#080808]">
          <div className="flex flex-col md:flex-row md:items-center justify-between p-4 md:p-4 md:px-6 border-b border-white/10 gap-4">
            <div className="flex items-center gap-2 md:gap-4 overflow-x-auto no-scrollbar pb-1 md:pb-0">
              <button
                onClick={() => handleProtocolFilter('ALL')}
                className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border whitespace-nowrap ${protocolFilter === 'ALL' ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
              >
                ALL
              </button>

              <div className="h-4 w-px bg-white/10 shrink-0 mx-1" />

              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleProtocolFilter('LENDING')}
                  className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border ${protocolFilter === 'LENDING' ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
                >
                  LENDING
                </button>
                <div className="flex items-center gap-1">
                  {LENDING_PROTOCOL_GROUPS.map(p => (
                    <button
                      key={p}
                      onClick={() => handleProtocolFilter(p)}
                      className={`text-xs md:text-sm tracking-widest uppercase transition-colors px-3 py-1.5 rounded-sm border ${protocolFilter === p ? 'text-cyan-400 border-cyan-400/30 bg-cyan-400/5' : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}`}
                    >
                      {PROTOCOL_FILTER_LABELS[p] || p}
                    </button>
                  ))}
                </div>
              </div>

              <div className="h-4 w-px bg-white/10 shrink-0 mx-1" />

              <button
                disabled
                className="text-xs md:text-sm tracking-widest uppercase px-3 py-1.5 rounded-sm border shrink-0 text-gray-600 border-transparent cursor-not-allowed opacity-50"
              >
                YIELDS (SOON)
              </button>
            </div>
          </div>

          <div className="w-full overflow-x-auto">
            <div className="min-w-[1000px] flex flex-col">
              {/* Table Header */}
              <div className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-3 text-[11px] md:text-[13px] text-gray-500 uppercase tracking-widest border-b border-white/10 bg-[#050505]`}>
                <div className="col-span-2">{renderSortHeader("name", protocolFilter === 'LENDING' ? 'Protocol' : 'Asset', "justify-start text-left")}</div>
                <div>{renderSortHeader("netWorth", "Liquidity")}</div>
                <div>{renderSortHeader("supplyUsd", "Total Supply")}</div>
                <div>{renderSortHeader("borrowUsd", "Total Borrow")}</div>
                <div>{renderSortHeader("supplyApy", "Supply APY")}</div>
                <div>{renderSortHeader("borrowApy", "Borrow APY")}</div>
                <div>{renderSortHeader("utilization", "Utilization")}</div>
                {showProtocolColumn && <div>{renderSortHeader("protocol", "Protocol")}</div>}

              </div>

              {/* Table Body */}
              <div className="flex flex-col divide-y divide-white/5 relative min-h-[200px]">
                {loading && marketsData.length === 0 ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center mt-12">
                    <Loader2 className="w-6 h-6 text-cyan-500 animate-spin mb-2" />
                  </div>
                ) : (
                  paginatedMarkets.map((pool, idx) => {
                    const isAggregate = Boolean(pool.isProtocolAggregate);
                    const canNavigate = isAggregate || Boolean(pool.entityId);
                    return (
                      <div
                        key={`${pool.protocol}-${pool.entityId || pool.symbol}-${idx}`}
                        onClick={() => {
                          if (isAggregate) {
                            navigate(`/data/${protocolSlugForApiProtocol(pool.protocol)}`);
                            return;
                          }
                          if (pool.entityId) navigate(marketRouteFor(pool.protocol, pool.entityId));
                        }}
                        className={`grid ${tableGridClass} gap-4 px-4 md:px-6 py-4 items-center transition-colors ${canNavigate ? 'hover:bg-white/[0.02] cursor-pointer' : 'opacity-50 cursor-not-allowed'
                          }`}
                      >
                        <div className="col-span-2 flex items-center gap-3">
                          {isAggregate ? (
                            <>
                              <div className="w-8 h-8 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-1 shadow-sm">
                                <img
                                  src={getProtocolIcon(pool.protocol)}
                                  alt={`${protocolLabel(pool.protocol)} logo`}
                                  className="w-full h-full object-contain rounded-full"
                                />
                              </div>
                              <div className="flex flex-col">
                                <span className="text-sm text-white font-medium">{protocolLabel(pool.protocol)}</span>
                                <span className="text-[10px] text-gray-600 uppercase tracking-widest">
                                  {pool.marketCount} markets
                                </span>
                              </div>
                            </>
                          ) : pool.collateralSymbol && protocolGroup(pool.protocol) === 'MORPHO' ? (
                            <>
                              <div className="flex items-center -space-x-2">
                                <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm z-10">
                                  <img src={getTokenIcon(pool.collateralSymbol)} alt={pool.collateralSymbol} className="w-full h-full object-contain rounded-full" />
                                </div>
                                <div className="w-6 h-6 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm">
                                  <img src={getTokenIcon(pool.symbol)} alt={pool.symbol} className="w-full h-full object-contain rounded-full" />
                                </div>
                              </div>
                              <span className="text-sm text-white font-medium">{pool.collateralSymbol}<span className="text-gray-500"> / {pool.symbol}</span></span>
                            </>
                          ) : (
                            <>
                              <div className="w-8 h-8 rounded-full bg-[#151515] border border-[#0a0a0a] flex items-center justify-center p-0.5 shadow-sm">
                                <img src={getTokenIcon(pool.symbol)} alt={pool.symbol} className="w-full h-full object-contain rounded-full" />
                              </div>
                              <span className="text-sm text-white font-medium">{pool.symbol}</span>
                            </>
                          )}
                        </div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.netWorth)}</div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.supplyUsd)}</div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-white tracking-widest">{formatCurrency(pool.borrowUsd)}</div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-green-500 tracking-widest">{formatApy(pool.supplyApy)}</div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-cyan-500 tracking-widest">{formatApy(pool.borrowApy)}</div>
                        <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-300 tracking-widest">{formatPercent(pool.utilization)}</div>
                        {showProtocolColumn && <div className="flex justify-center text-center text-[10px] md:text-[13px] text-gray-400 tracking-widest">{protocolLabel(pool.protocol)}</div>}
                      </div>
                    );
                  })
                )}
              </div>

              {/* Pagination Controls */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-6 px-4 md:px-6 py-4 border-t border-white/10 bg-[#080808]">
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    Page {safeCurrentPage} of {totalPages}
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setCurrentPage(safeCurrentPage - 1)}
                      disabled={safeCurrentPage === 1}
                      className="px-3 py-1 bg-[#111] border border-white/10 text-xs text-gray-300 uppercase tracking-widest hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Prev
                    </button>
                    <button
                      onClick={() => setCurrentPage(safeCurrentPage + 1)}
                      disabled={safeCurrentPage === totalPages}
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
