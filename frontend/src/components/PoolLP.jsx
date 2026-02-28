import React, { useState, useMemo, useEffect, useCallback } from "react";
import { ethers } from "ethers";
import {
  Droplets,
  Activity,
  TrendingUp,
  ArrowUpDown,
  ExternalLink,
  ChevronDown,
  Loader2,
  Layers,
  Wallet,
} from "lucide-react";

import { useSimulation } from "../hooks/useSimulation";
import { useWallet } from "../context/WalletContext";
import { useBrokerAccount } from "../hooks/useBrokerAccount";
import TradingTerminal, { InputGroup, SummaryRow } from "./TradingTerminal";
import StatItem from "./StatItem";
import RLDPerformanceChart from "./RLDChart";
import ClaimFeesModal from "./ClaimFeesModal";
import WithdrawModal from "./WithdrawModal";
import AddLiquidityModal from "./AddLiquidityModal";

const RPC_URL = `${window.location.origin}/rpc`;
const ERC20_BALANCE_ABI = ["function balanceOf(address) view returns (uint256)"];

// ── Mock Liquidity Distribution Generator ─────────────────────
// Simulates a realistic concentrated liquidity profile:
// base full-range liquidity + two concentrated humps near the current price.
function generateMockLiquidityBins(currentPrice, numBins = 80) {
  if (!currentPrice || currentPrice <= 0) return [];

  const spread = currentPrice * 0.15; // ±15% around current price
  const minP = currentPrice - spread;
  const maxP = currentPrice + spread;
  const binWidth = (maxP - minP) / numBins;

  const BASE_LIQ = 8_000_000_000_000; // flat full-range base
  const bins = [];

  for (let i = 0; i < numBins; i++) {
    const priceMid = minP + (i + 0.5) * binWidth;

    // Concentrated hump #1: tight around current price
    const dist1 = Math.abs(priceMid - currentPrice) / (spread * 0.25);
    const hump1 = Math.exp(-(dist1 * dist1) / 2) * 18_000_000_000_000;

    // Concentrated hump #2: slightly below current price (asymmetric)
    const hump2Center = currentPrice - spread * 0.2;
    const dist2 = Math.abs(priceMid - hump2Center) / (spread * 0.15);
    const hump2 = Math.exp(-(dist2 * dist2) / 2) * 6_000_000_000_000;

    // Small noise for realism
    const noise = (Math.random() - 0.5) * BASE_LIQ * 0.08;

    bins.push({
      price: priceMid.toFixed(3),
      priceFrom: minP + i * binWidth,
      priceTo: minP + (i + 1) * binWidth,
      liquidity: Math.max(0, Math.round(BASE_LIQ + hump1 + hump2 + noise)),
    });
  }
  return bins;
}


function catmullRomToBezier(points, tension = 0.5) {
  if (points.length < 2) return "";
  const d = [`M ${points[0].x},${points[0].y}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const cp1x = p1.x + (p2.x - p0.x) / (6 * tension);
    const cp1y = p1.y + (p2.y - p0.y) / (6 * tension);
    const cp2x = p2.x - (p3.x - p1.x) / (6 * tension);
    const cp2y = p2.y - (p3.y - p1.y) / (6 * tension);
    d.push(`C ${cp1x},${cp1y} ${cp2x},${cp2y} ${p2.x},${p2.y}`);
  }
  return d.join(" ");
}

// ── Combo: Dotted Bars + Mountain Fill ────────────────────────
function ComboChart({ bins, currentPrice }) {
  const containerRef = React.useRef(null);
  const [dims, setDims] = React.useState({ width: 0, height: 0 });
  const [hoveredIdx, setHoveredIdx] = React.useState(null);

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setDims({ width, height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!bins || bins.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="animate-spin text-gray-700" />
      </div>
    );
  }

  // Subtract the floor so the chart shows the concentrated delta above baseline
  const minLiq = Math.min(...bins.map((b) => b.liquidity));
  const deltas = bins.map((b) => b.liquidity - minLiq);
  const maxDelta = Math.max(...deltas);
  const currentBinIdx = bins.findIndex(
    (b) => currentPrice >= b.priceFrom && currentPrice < b.priceTo,
  );

  // Layout — match the bar chart spacing
  const MARGIN = { top: 10, right: 8, bottom: 24, left: 0 };
  const Y_AXIS_W = 48;
  const plotW = dims.width - MARGIN.left - MARGIN.right - Y_AXIS_W;
  const plotH = dims.height - MARGIN.top - MARGIN.bottom;
  const binH = plotH / bins.length;

  const priceToY = (idx) => MARGIN.top + idx * binH + binH / 2;
  const liqToX = (delta) => MARGIN.left + (maxDelta > 0 ? (delta / maxDelta) * plotW * 0.75 : 0);

  // Build smooth spline points
  const curvePoints = bins.map((b, i) => ({
    x: liqToX(deltas[i]),
    y: priceToY(i),
  }));
  const smoothOutline = catmullRomToBezier(curvePoints);

  // Closed area path: left wall → smooth curve → left wall
  const smoothFill = `M ${MARGIN.left},${priceToY(0)} ` +
    `L ${curvePoints[0].x},${curvePoints[0].y} ` +
    smoothOutline.slice(smoothOutline.indexOf("C")) +
    ` L ${MARGIN.left},${priceToY(bins.length - 1)} Z`;

  // Determine which dotted bars to draw (every bin at density 1)
  const drawEvery = 1;

  return (
    <div ref={containerRef} className="w-full h-full relative font-mono" onMouseLeave={() => setHoveredIdx(null)}>
      {dims.width > 0 && plotH > 0 && (
        <svg width={dims.width} height={dims.height}>
          <defs>
            <linearGradient id="combo-fill" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.05} />
              <stop offset="100%" stopColor="#7c3aed" stopOpacity={0.3} />
            </linearGradient>
            <filter id="combo-glow">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Vertical grid lines */}
          {[0.25, 0.5, 0.75, 1].map((frac) => (
            <line
              key={frac}
              x1={MARGIN.left + frac * plotW}
              y1={MARGIN.top}
              x2={MARGIN.left + frac * plotW}
              y2={MARGIN.top + plotH}
              stroke="#27272a"
              strokeDasharray="3 3"
            />
          ))}

          {/* Smooth mountain area fill */}
          <path d={smoothFill} fill="url(#combo-fill)" />

          {/* Smooth mountain outline */}
          <path
            d={smoothOutline}
            fill="none"
            stroke="#7c3aed"
            strokeWidth={1.5}
            strokeOpacity={0.6}
          />

          {/* Dotted horizontal bars — every bin */}
          {bins.map((bin, i) => {
            const isCurrent = i === currentBinIdx;
            const isHovered = i === hoveredIdx;
            if (!isCurrent && !isHovered && i % drawEvery !== 0) return null;
            const y = priceToY(i);
            const endX = liqToX(deltas[i]);

            return (
              <g key={i}>
                <line
                  x1={MARGIN.left}
                  y1={y}
                  x2={endX}
                  y2={y}
                  stroke={isCurrent ? "#22d3ee" : isHovered ? "#c4b5fd" : "#a78bfa"}
                  strokeWidth={isCurrent ? 2 : isHovered ? 1.5 : 0.5}
                  strokeDasharray={isCurrent ? "6 3" : isHovered ? "4 2" : "2 3"}
                  strokeOpacity={isCurrent ? 1 : isHovered ? 0.8 : 0.35}
                  filter={isCurrent ? "url(#combo-glow)" : undefined}
                />
                {(isCurrent || isHovered) && (
                  <circle
                    cx={endX}
                    cy={y}
                    r={isCurrent ? 3 : 2}
                    fill={isCurrent ? "#22d3ee" : "#c4b5fd"}
                  />
                )}
              </g>
            );
          })}

          {/* Invisible hover rects for each bin */}
          {bins.map((_, i) => (
            <rect
              key={`hover-${i}`}
              x={0}
              y={MARGIN.top + i * binH}
              width={dims.width}
              height={binH}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          ))}

          {/* Current price dashed line across full width */}
          {currentBinIdx >= 0 && (
            <line
              x1={MARGIN.left}
              y1={priceToY(currentBinIdx)}
              x2={MARGIN.left + plotW}
              y2={priceToY(currentBinIdx)}
              stroke="#22d3ee"
              strokeWidth={1}
              strokeDasharray="4 4"
              strokeOpacity={0.6}
            />
          )}

          {/* Y-axis labels (price, right side) */}
          {bins.map((bin, i) => {
            if (i % Math.max(1, Math.floor(bins.length / 10)) !== 0 && i !== currentBinIdx) return null;
            return (
              <text
                key={i}
                x={MARGIN.left + plotW + 6}
                y={priceToY(i) + 3}
                fill={i === currentBinIdx ? "#22d3ee" : "#71717a"}
                fontSize={11}
                fontFamily="monospace"
              >
                ${Number(bin.price).toFixed(2)}
              </text>
            );
          })}

          {/* X-axis labels (match bar chart format) */}
          {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
            const val = frac * maxDelta;
            const t = val / 1e12;
            let label = "";
            if (t >= 1) label = `$${t.toFixed(0)}T`;
            else if (val >= 1e9) label = `$${(val / 1e9).toFixed(0)}B`;
            else if (val >= 1e6) label = `$${(val / 1e6).toFixed(0)}M`;
            return (
              <text
                key={frac}
                x={MARGIN.left + frac * plotW}
                y={MARGIN.top + plotH + 16}
                textAnchor="middle"
                fill="#71717a"
                fontSize={11}
                fontFamily="monospace"
              >
                {label}
              </text>
            );
          })}

          {/* Bottom axis line */}
          <line
            x1={MARGIN.left}
            y1={MARGIN.top + plotH}
            x2={MARGIN.left + plotW}
            y2={MARGIN.top + plotH}
            stroke="#27272a"
          />
        </svg>
      )}

      {/* Tooltip */}
      {hoveredIdx !== null && dims.width > 0 && (() => {
        const bin = bins[hoveredIdx];
        const y = priceToY(hoveredIdx);
        const x = liqToX(deltas[hoveredIdx]);
        const liqVal = bin.liquidity / 1e6;
        const liqStr = liqVal >= 1000 ? `$${(liqVal / 1000).toFixed(1)}B` : `$${liqVal.toFixed(1)}M`;
        const above = y > dims.height / 2;
        return (
          <div
            className="absolute pointer-events-none bg-zinc-950/95 border border-zinc-800 px-3 py-2 text-xs font-mono shadow-xl"
            style={{
              left: Math.min(x + 8, dims.width - 160),
              top: above ? y - 58 : y + 8,
            }}
          >
            <div className="text-zinc-400 mb-1">
              ${Number(bin.priceFrom).toFixed(2)} &ndash; ${Number(bin.priceTo).toFixed(2)}
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-violet-500 inline-block" />
              <span className="text-zinc-300">Liquidity:</span>
              <span className="text-white font-semibold">{liqStr}</span>
            </div>
          </div>
        );
      })()}
    </div>
  );
}




export default function PoolLP() {
  // ── Wallet & broker ─────────────────────────────────────────
  const { account } = useWallet();

  // ── Simulation data ─────────────────────────────────────────
  const sim = useSimulation({ pollInterval: 2000 });
  const {
    connected,
    loading,
    error,
    market,
    pool,
    funding,
    fundingFromNF: _fundingFromNF,
    volumeData,
    protocolStats,
    marketInfo,
    chartData,
  } = sim;

  const { hasBroker, brokerAddress } = useBrokerAccount(
    account,
    marketInfo?.broker_factory,
    marketInfo?.collateral?.address,
  );

  // ── Mock liquidity depth distribution ───────────────────────
  const liquidityBins = useMemo(
    () => generateMockLiquidityBins(pool?.markPrice || 0, 40),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pool?.markPrice ? Math.round(pool.markPrice * 10) / 10 : 0],
  );

  // ── Local UI state ──────────────────────────────────────────
  const [activeTab, setActiveTab] = useState("ADD");
  const [token0Amount, setToken0Amount] = useState("");
  const [token1Amount, setToken1Amount] = useState("");
  const [minPrice, setMinPrice] = useState("0.95");
  const [maxPrice, setMaxPrice] = useState("1.05");
  const [removePercent, setRemovePercent] = useState(100);
  const [selectedPosition, _setSelectedPosition] = useState(null);
  const [actionDropdown, setActionDropdown] = useState(null);
  const [claimPosition, setClaimPosition] = useState(null);
  const [withdrawPosition, setWithdrawPosition] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [chartView, setChartView] = useState("PRICE");

  // ── Account selector (wallet vs broker) ─────────────────────
  const [accountDropdownOpen, setAccountDropdownOpen] = useState(false);
  // "wallet" | "broker"
  const [selectedAccount, setSelectedAccount] = useState("broker");
  const activeAddress = selectedAccount === "broker" && brokerAddress ? brokerAddress : account;

  // ── Token balances for selected account ─────────────────────
  const [token0Balance, setToken0Balance] = useState(null); // wRLP
  const [token1Balance, setToken1Balance] = useState(null); // waUSDC

  const token0Addr = marketInfo?.position_token?.address;
  const token1Addr = marketInfo?.collateral?.address;

  const fetchBalances = useCallback(async () => {
    if (!activeAddress || !token0Addr || !token1Addr) {
      setToken0Balance(null);
      setToken1Balance(null);
      return;
    }
    try {
      const provider = new ethers.JsonRpcProvider(RPC_URL);
      const t0 = new ethers.Contract(token0Addr, ERC20_BALANCE_ABI, provider);
      const t1 = new ethers.Contract(token1Addr, ERC20_BALANCE_ABI, provider);
      const [b0, b1] = await Promise.all([
        t0.balanceOf(activeAddress),
        t1.balanceOf(activeAddress),
      ]);
      setToken0Balance(parseFloat(ethers.formatUnits(b0, 6)));
      setToken1Balance(parseFloat(ethers.formatUnits(b1, 6)));
    } catch (e) {
      console.warn("Balance fetch failed:", e);
    }
  }, [activeAddress, token0Addr, token1Addr]);

  useEffect(() => {
    fetchBalances();
    const id = setInterval(fetchBalances, 5000);
    return () => clearInterval(id);
  }, [fetchBalances]);

  // ── Derived pool data from simulation ───────────────────────
  const poolData = useMemo(() => {
    if (!pool || !market || !marketInfo) return null;

    const token0Symbol = marketInfo.position_token?.symbol || "wRLP";
    const token1Symbol = marketInfo.collateral?.symbol || "waUSDC";
    const feeTier = marketInfo.infrastructure?.pool_fee || 500;
    const feePercent = feeTier / 10000; // 500 → 0.05%

    // TVL estimate: liquidity value in USD terms
    // Both tokens have 6 decimals; liquidity is raw V4 liquidity units
    // Rough estimate: use mark price × liquidity converted
    const rawLiquidity = pool.liquidity || 0;
    const tvl = protocolStats?.totalCollateral || 0; // waUSDC collateral as TVL proxy

    const volume24h = volumeData?.volume_usd || 0;
    const swapCount = volumeData?.swap_count || 0;

    // Fees estimated from volume × fee rate
    const fees24h = volume24h * (feeTier / 1e6);
    const fees7d = fees24h * 7;

    // APR: annualized fees / TVL
    const apr = tvl > 0 ? ((fees24h * 365) / tvl) * 100 : 0;
    const aprWeekly = apr / 52;

    // Funding
    const fundingRate = funding?.spreadPct || 0;
    const fundingDirection = funding?.direction === "LONGS_PAY" ? "longs" : "shorts";

    // Hook address
    const hookAddr = marketInfo.infrastructure?.twamm_hook || "";
    const hookShort = hookAddr
      ? `${hookAddr.slice(0, 6)}...${hookAddr.slice(-4)}`
      : "—";

    return {
      pair: `${token0Symbol} / ${token1Symbol}`,
      protocol: "Uniswap V4",
      hookAddress: hookShort,
      hookAddressFull: hookAddr,
      feeTier: `${feePercent.toFixed(2)}%`,
      tickSpacing: marketInfo.infrastructure?.tick_spacing || 5,
      tvl,
      volume24h,
      swapCount,
      fees24h,
      fees7d,
      apr,
      aprWeekly,
      aprYearly: apr,
      currentTick: pool.tick,
      currentPrice: pool.markPrice,
      indexPrice: market.indexPrice,
      markPrice: pool.markPrice,
      fundingRate: Math.abs(fundingRate),
      fundingDirection,
      activeLiquidity: rawLiquidity,
      token0: {
        symbol: token0Symbol,
        name: marketInfo.position_token?.name || "Wrapped RLP",
        decimals: 6,
      },
      token1: {
        symbol: token1Symbol,
        name: marketInfo.collateral?.name || "Wrapped aUSDC",
        decimals: 6,
      },
    };
  }, [pool, market, marketInfo, funding, volumeData, protocolStats]);

  // ── Chart configuration ─────────────────────────────────────
  const CHART_VIEWS = useMemo(
    () => ({
      PRICE: {
        label: "Price",
        areas: [
          { key: "indexPrice", name: "Index Price", color: "#22d3ee" },
          { key: "markPrice", name: "Mark Price", color: "#ec4899" },
        ],
      },
      LIQUIDITY: {
        label: "Liquidity",
        areas: [
          { key: "liquidity", name: "Active Liq", color: "#a855f7" },
        ],
      },
      VOLUME: {
        label: "Volume",
        areas: [
          { key: "volume", name: "Volume", color: "#22c55e" },
        ],
      },
    }),
    [],
  );

  const activeChartConfig = CHART_VIEWS[chartView];

  // ── User LP positions (empty until V4 PositionManager hook is built) ──
  const userPositions = [];

  // ── Error / Loading states ──────────────────────────────────
  if (error && !connected) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="text-red-500 text-sm uppercase tracking-widest">
            SIM_DISCONNECTED
          </div>
          <div className="text-gray-600 text-sm max-w-xs">
            Cannot reach simulation indexer. Make sure the Docker simulation
            stack is running.
          </div>
          <div className="text-sm text-gray-700 font-mono">
            Expected at: http://localhost:8080
          </div>
        </div>
      </div>
    );
  }

  if (loading || !poolData) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-6 h-6 text-cyan-500 animate-spin" />
          <span className="text-sm uppercase tracking-widest text-gray-500">
            Connecting to simulation...
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          {/* === LEFT COLUMN (Span 9) === */}
          <div className="xl:col-span-9 flex flex-col gap-4">
            {/* 1. METRICS GRID */}
            <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
              {/* Branding */}
              <div className="lg:col-span-4 flex flex-col justify-between p-6 border-b lg:border-b-0 lg:border-r border-white/10 h-full min-h-[180px]">
                <div>
                  <div className="text-sm text-gray-700 mb-6 font-mono leading-tight tracking-tight">
                    {poolData.hookAddress}
                  </div>
                  <h2 className="text-3xl font-medium tracking-tight mb-2 leading-none">
                    {poolData.pair}
                    <br />
                    <span className="text-gray-600 uppercase">Liquidity Pool</span>
                  </h2>
                </div>
                <div className="mt-auto pt-4 border-t border-white/10 flex items-center justify-between">
                  <span className="text-sm uppercase tracking-widest text-gray-500">
                    {poolData.protocol}
                  </span>
                  <a
                    href={`https://etherscan.io/address/${poolData.hookAddressFull}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm uppercase tracking-widest text-purple-400 font-mono flex items-center gap-1 hover:text-purple-300 transition-colors"
                  >
                    <Droplets size={10} />
                    V4_HOOK
                    <ExternalLink size={9} className="opacity-60" />
                  </a>
                </div>
              </div>

              {/* Stats Cards — 3 panels */}
              <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                {/* PRICE */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    PRICE <ArrowUpDown size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="INDEX"
                      value={poolData.indexPrice.toFixed(4)}
                    />
                    <StatItem
                      label="MARK"
                      value={poolData.markPrice.toFixed(4)}
                    />
                    <div className="col-span-2">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">Funding</div>
                      <div className="flex items-baseline gap-2">
                        <span className={`text-sm font-light tracking-tight ${poolData.fundingDirection === "longs" ? "text-red-400" : "text-green-400"}`}>
                          {poolData.fundingRate.toFixed(4)}%
                        </span>
                        <span className="text-sm text-gray-500 uppercase tracking-widest">
                          {poolData.fundingDirection === "longs" ? "Longs pay Shorts" : "Shorts pay Longs"}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* POOL */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    POOL <Droplets size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="TVL"
                      value={poolData.tvl >= 1e6
                        ? `$${(poolData.tvl / 1e6).toFixed(2)}M`
                        : poolData.tvl >= 1e3
                          ? `$${(poolData.tvl / 1e3).toFixed(0)}K`
                          : `$${poolData.tvl.toFixed(0)}`}
                    />
                    <StatItem
                      label="VOLUME"
                      value={volumeData?.volume_formatted || "—"}
                    />
                    <StatItem
                      label="FEES_24H"
                      value={poolData.fees24h >= 1e3
                        ? `$${(poolData.fees24h / 1e3).toFixed(1)}K`
                        : `$${poolData.fees24h.toFixed(0)}`}
                    />
                    <StatItem label="FEE" value={poolData.feeTier} />
                  </div>
                </div>

                {/* YIELD APR */}
                <div className="p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px]">
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-4 flex justify-between">
                    YIELD APR <TrendingUp size={15} className="opacity-90" />
                  </div>
                  <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                    <StatItem
                      label="WEEKLY"
                      value={`${poolData.aprWeekly.toFixed(2)}%`}
                      valueClassName="text-green-400"
                    />
                    <StatItem
                      label="YEARLY"
                      value={`${poolData.aprYearly.toFixed(1)}%`}
                      valueClassName="text-green-400"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 2. CHART */}
            <div className="relative flex-1 min-h-[350px] md:min-h-[400px] border border-white/10">
              {/* Chart Header — series legend left, view tabs right */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-white/10 bg-[#0a0a0a]">
                {/* LEFT: Series legend */}
                <div className="flex items-center gap-5">
                  {activeChartConfig.areas.map((s) => (
                    <div
                      key={s.key}
                      className="flex items-center gap-2"
                    >
                      <div
                        className="w-2 h-2"
                        style={{ backgroundColor: s.color }}
                      />
                      <span className="text-sm uppercase tracking-widest text-gray-400">
                        {s.name}
                      </span>
                    </div>
                  ))}
                </div>

                {/* RIGHT: View switcher */}
                <div className="flex items-center gap-1">
                  {Object.entries(CHART_VIEWS).map(([key, view]) => (
                    <button
                      key={key}
                      onClick={() => setChartView(key)}
                      className={`px-3 py-1 text-sm font-semibold uppercase tracking-widest transition-colors ${
                        chartView === key
                          ? "text-white bg-white/10"
                          : "text-gray-600 hover:text-gray-400"
                      }`}
                    >
                      {view.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Chart body */}
              <div className="h-[350px] md:h-[500px] w-full p-4">
                {chartView === "LIQUIDITY" ? (
                  <ComboChart
                    bins={liquidityBins}
                    currentPrice={poolData?.markPrice || 0}
                  />
                ) : chartData.length === 0 ? (
                  <div className="h-full flex items-center justify-center">
                    <Loader2 className="animate-spin text-gray-700" />
                  </div>
                ) : (
                  <RLDPerformanceChart
                    data={chartData}
                    areas={activeChartConfig.areas}
                    resolution="1D"
                  />
                )}
              </div>
            </div>
          </div>

          {/* === RIGHT COLUMN: TRADING TERMINAL (Span 3) === */}
          <TradingTerminal
            title="Pool_Liquidity"
            Icon={Droplets}
            account={account}
            subTitle={
              account ? (
                <div className="relative">
                  <button
                    onClick={() => setAccountDropdownOpen(!accountDropdownOpen)}
                    className="flex items-center gap-1.5 text-sm font-mono text-cyan-400 hover:text-cyan-300 transition-colors"
                  >
                    <Wallet size={12} />
                    {selectedAccount === "broker" && brokerAddress
                      ? `Broker #1`
                      : `0x...${account.slice(-4)}`}
                    <ChevronDown size={12} className={`transition-transform ${accountDropdownOpen ? "rotate-180" : ""}`} />
                  </button>
                  {accountDropdownOpen && (
                    <div className="absolute right-0 top-full mt-2 z-50 border border-white/10 bg-[#0a0a0a] min-w-[220px]">
                      {/* Wallet option */}
                      <button
                        onClick={() => { setSelectedAccount("wallet"); setAccountDropdownOpen(false); }}
                        className={`w-full text-left px-4 py-2.5 text-sm font-mono hover:bg-white/5 transition-colors border-b border-white/5 flex items-center justify-between ${
                          selectedAccount === "wallet" ? "text-cyan-400" : "text-gray-400"
                        }`}
                      >
                        <span>Wallet</span>
                        <span className="text-xs text-gray-600">{`0x...${account.slice(-4)}`}</span>
                      </button>
                      {/* Broker option (if exists) */}
                      {hasBroker && brokerAddress && (
                        <button
                          onClick={() => { setSelectedAccount("broker"); setAccountDropdownOpen(false); }}
                          className={`w-full text-left px-4 py-2.5 text-sm font-mono hover:bg-white/5 transition-colors border-b border-white/5 flex items-center justify-between ${
                            selectedAccount === "broker" ? "text-cyan-400" : "text-gray-400"
                          }`}
                        >
                          <span>Broker #1</span>
                          <span className="text-xs text-gray-600">{`0x...${brokerAddress.slice(-4)}`}</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <span className="text-sm text-gray-600 uppercase tracking-widest">V4</span>
              )
            }
            tabs={[
              {
                id: "ADD",
                label: "Add",
                onClick: () => setActiveTab("ADD"),
                isActive: activeTab === "ADD",
                color: "cyan",
              },
              {
                id: "REMOVE",
                label: "Remove",
                onClick: () => setActiveTab("REMOVE"),
                isActive: activeTab === "REMOVE",
                color: "pink",
              },
            ]}
            actionButton={{
              label: activeTab === "ADD" ? "Add Liquidity" : "Remove Liquidity",
              onClick: () => {
                if (activeTab === "ADD") {
                  setShowAddModal(true);
                } else if (activeTab === "REMOVE" && selectedPosition) {
                  setWithdrawPosition(selectedPosition);
                }
              },
              disabled: activeTab === "REMOVE" && !selectedPosition,
              variant: activeTab === "ADD" ? "cyan" : "pink",
            }}
            footer={null}
          >
            {/* === ADD LIQUIDITY === */}
            {activeTab === "ADD" && (
              <>
                {/* Price Range */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                      Price Range
                    </span>
                    <button
                      onClick={() => { setMinPrice("0.0001"); setMaxPrice("100"); }}
                      className="text-sm text-cyan-500 uppercase tracking-widest hover:text-cyan-400 transition-colors"
                    >
                      Full Range
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="border border-white/10 bg-[#060606] p-3">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                        Min Price
                      </div>
                      <input
                        type="number"
                        value={minPrice}
                        onChange={(e) => setMinPrice(e.target.value)}
                        className="w-full bg-transparent text-white text-sm font-mono focus:outline-none"
                        placeholder="0.00"
                      />
                      <div className="text-sm text-gray-600 mt-1">
                        {poolData.token1.symbol} per {poolData.token0.symbol}
                      </div>
                    </div>
                    <div className="border border-white/10 bg-[#060606] p-3">
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                        Max Price
                      </div>
                      <input
                        type="number"
                        value={maxPrice}
                        onChange={(e) => setMaxPrice(e.target.value)}
                        className="w-full bg-transparent text-white text-sm font-mono focus:outline-none"
                        placeholder="0.00"
                      />
                      <div className="text-sm text-gray-600 mt-1">
                        {poolData.token1.symbol} per {poolData.token0.symbol}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Token Amounts */}
                <InputGroup
                  label={poolData.token0.symbol}
                  subLabel={`Balance: ${token0Balance != null ? token0Balance.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}`}
                  value={token0Amount}
                  onChange={setToken0Amount}
                  suffix={poolData.token0.symbol}
                  onMax={token0Balance > 0 ? () => setToken0Amount(String(token0Balance)) : undefined}
                />
                <InputGroup
                  label={poolData.token1.symbol}
                  subLabel={`Balance: ${token1Balance != null ? token1Balance.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}`}
                  value={token1Amount}
                  onChange={setToken1Amount}
                  suffix={poolData.token1.symbol}
                  onMax={token1Balance > 0 ? () => setToken1Amount(String(token1Balance)) : undefined}
                />

                {/* Summary */}
                <div className="space-y-2 pt-2 border-t border-white/10">
                  <SummaryRow label="Pool" value={poolData.pair} />
                  <SummaryRow label="Fee Tier" value={poolData.feeTier} />
                  <SummaryRow
                    label="Current Price"
                    value={poolData.currentPrice.toFixed(4)}
                  />
                  <SummaryRow
                    label="Est. APR"
                    value={`${poolData.apr.toFixed(1)}%`}
                    valueColor="text-green-400"
                  />
                </div>
              </>
            )}

            {/* === REMOVE LIQUIDITY === */}
            {activeTab === "REMOVE" && (
              <>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                      Select Position
                    </span>
                  </div>

                  {/* Empty state — V4 position queries require a new hook */}
                  {userPositions.length === 0 && (
                    <div className="border border-white/10 bg-[#060606] p-6 text-center">
                      <Layers size={24} className="mx-auto text-gray-700 mb-3" />
                      <div className="text-sm text-gray-500 uppercase tracking-widest mb-1">
                        No Positions Found
                      </div>
                      <div className="text-sm text-gray-700">
                        LP position queries from V4 PositionManager coming soon
                      </div>
                    </div>
                  )}
                </div>

                {/* Remove controls (only shown when a position is selected) */}
                {selectedPosition && (
                  <>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm uppercase tracking-widest font-bold text-gray-500">
                          Amount
                        </span>
                        <span className="text-xl font-mono text-white">
                          {removePercent}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="100"
                        value={removePercent}
                        onChange={(e) =>
                          setRemovePercent(Number(e.target.value))
                        }
                        className="w-full accent-pink-500 h-1"
                      />
                      <div className="grid grid-cols-4 gap-2">
                        {[25, 50, 75, 100].map((pct) => (
                          <button
                            key={pct}
                            onClick={() => setRemovePercent(pct)}
                            className={`py-1.5 text-sm font-bold uppercase tracking-widest border transition-colors ${
                              removePercent === pct
                                ? "border-pink-500/50 text-pink-400 bg-pink-500/10"
                                : "border-white/10 text-gray-500 hover:text-white hover:border-white/20"
                            }`}
                          >
                            {pct}%
                          </button>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </>
            )}
          </TradingTerminal>
        </div>

        {/* 3. POSITIONS TABLE */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-9 border border-white/10">
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-bold uppercase tracking-widest">
                    Your Positions
                  </h3>
                </div>
                <div className="text-sm text-gray-500 uppercase tracking-widest flex items-center gap-2">
                  <Activity size={12} />
                  {userPositions.length > 0 ? "ACTIVE" : "NONE"}
                </div>
              </div>

              {/* Empty state */}
              {userPositions.length === 0 && (
                <div className="px-6 py-12 text-center">
                  <Layers size={32} className="mx-auto text-gray-700 mb-4" />
                  <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                    No LP Positions
                  </div>
                  <div className="text-sm text-gray-700 max-w-sm mx-auto">
                    V4 PositionManager integration coming soon. Add liquidity using the panel on the right.
                  </div>
                </div>
              )}

              {/* Table Header — shown when positions exist */}
              {userPositions.length > 0 && (
                <>
                  <div className="hidden md:grid grid-cols-12 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 text-center">
                    <div className="col-span-1 text-left">#</div>
                    <div className="col-span-2 text-left">Range</div>
                    <div className="col-span-2">Liquidity</div>
                    <div className="col-span-2">Token 0</div>
                    <div className="col-span-2">Token 1</div>
                    <div className="col-span-2">Fees Earned</div>
                    <div className="col-span-1">Action</div>
                  </div>

                  {userPositions.map((pos) => (
                    <div key={pos.id}>
                      <div
                        className="grid grid-cols-1 md:grid-cols-12 gap-4 px-6 py-4 hover:bg-white/[0.02] transition-colors border-b border-white/5 last:border-b-0 items-center text-center"
                      >
                        <div className="col-span-1 text-sm text-gray-500 font-mono text-left">
                          {pos.id}
                        </div>
                        <div className="col-span-2 text-left">
                          <div className="text-sm font-mono text-white">
                            {pos.priceLower?.toFixed(4)} –{" "}
                            {pos.priceUpper?.toFixed(4)}
                          </div>
                        </div>
                        <div className="col-span-2 text-sm font-mono text-white">
                          ${pos.liquidity}
                        </div>
                        <div className="col-span-2 text-sm font-mono text-white">
                          {pos.token0Amount}{" "}
                          <span className="text-gray-500 text-sm">
                            {poolData.token0.symbol}
                          </span>
                        </div>
                        <div className="col-span-2 text-sm font-mono text-white">
                          {pos.token1Amount}{" "}
                          <span className="text-gray-500 text-sm">
                            {poolData.token1.symbol}
                          </span>
                        </div>
                        <div className="col-span-2 text-sm font-mono">
                          <span className="text-green-400">
                            +${pos.feesEarned}
                          </span>
                        </div>
                        <div className="col-span-1 relative flex justify-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setActionDropdown(actionDropdown === pos.id ? null : pos.id);
                            }}
                            className="p-1.5 text-gray-600 hover:text-white hover:bg-white/5 transition-colors"
                          >
                            <ChevronDown size={16} className={`transition-transform ${actionDropdown === pos.id ? 'rotate-180' : ''}`} />
                          </button>
                          {actionDropdown === pos.id && (
                            <div className="absolute right-0 top-full mt-1 z-50 border border-white/10 bg-[#0a0a0a] backdrop-blur-sm min-w-[150px]">
                              <button
                                onClick={() => {
                                  setActionDropdown(null);
                                  setClaimPosition(pos);
                                }}
                                className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors font-mono"
                              >
                                Claim Fees
                              </button>
                              <button
                                onClick={() => {
                                  setActionDropdown(null);
                                  setWithdrawPosition(pos);
                                }}
                                className="w-full text-left px-4 py-2 text-sm text-white hover:bg-white/5 transition-colors border-t border-white/5 font-mono"
                              >
                                Withdraw
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </>
              )}
          </div>{/* close col-span-9 */}
        </div>{/* close grid */}
      </div>{/* close max-w container */}

      {/* Claim Fees Modal */}
      <ClaimFeesModal
        isOpen={!!claimPosition}
        onClose={() => setClaimPosition(null)}
        onConfirm={() => {
          // TODO: execute claim transaction
          setClaimPosition(null);
        }}
        position={claimPosition}
        token0={poolData.token0}
        token1={poolData.token1}
      />

      {/* Withdraw Modal */}
      <WithdrawModal
        isOpen={!!withdrawPosition}
        onClose={() => setWithdrawPosition(null)}
        onConfirm={() => {
          // TODO: execute withdraw transaction
          setWithdrawPosition(null);
        }}
        position={withdrawPosition}
        token0={poolData.token0}
        token1={poolData.token1}
      />

      {/* Add Liquidity Modal */}
      <AddLiquidityModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        onConfirm={() => {
          // TODO: execute add liquidity transaction
          setShowAddModal(false);
        }}
        minPrice={minPrice}
        maxPrice={maxPrice}
        token0Amount={token0Amount}
        token1Amount={token1Amount}
        token0={poolData.token0}
        token1={poolData.token1}
        pool={poolData}
      />
    </div>
  );
}
