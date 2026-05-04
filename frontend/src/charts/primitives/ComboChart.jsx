import React, { useRef, useState, useEffect } from "react";
import { Loader2 } from "lucide-react";

/**
 * ComboChart — SVG dual-color liquidity distribution chart.
 *
 * Shared between Pools and Perps pages. Shows liquidity depth at each
 * price bin, split into pink (below current price) and cyan (above).
 *
 * @param {{ bins: Array, currentPrice: number }} props
 */
export default function ComboChart({ bins, currentPrice }) {
  const containerRef = useRef(null);
  const [dims, setDims] = useState({ width: 0, height: 0 });
  const [hoveredIdx, setHoveredIdx] = useState(null);

  useEffect(() => {
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

  // Use combined token amounts for Y-axis height (like Uniswap)
  const getDepth = (b) => (b.amount0 ?? 0) + (b.amount1 ?? 0);
  const maxDepth = Math.max(...bins.map(getDepth), 0.01);

  // Layout
  const MARGIN = { top: 16, right: 12, bottom: 30, left: 56 };
  const plotW = dims.width - MARGIN.left - MARGIN.right;
  const plotH = dims.height - MARGIN.top - MARGIN.bottom;
  const barW = plotW / bins.length;

  const xOf = (idx) => MARGIN.left + idx * barW + barW / 2;
  const yOf = (depth) => MARGIN.top + plotH - (maxDepth > 0 ? (depth / maxDepth) * plotH * 0.8 : 0);

  // Current price X (interpolated)
  let curPriceX = null;
  let curBinIdx = -1;
  if (currentPrice && bins.length > 0) {
    const minP = bins[0].priceFrom;
    const maxP = bins[bins.length - 1].priceTo;
    if (currentPrice >= minP && currentPrice <= maxP) {
      curBinIdx = bins.findIndex(b => currentPrice >= b.priceFrom && currentPrice < b.priceTo);
      if (curBinIdx < 0) curBinIdx = bins.length - 1;
      const bin = bins[curBinIdx];
      const fracInBin = (currentPrice - bin.priceFrom) / (bin.priceTo - bin.priceFrom);
      curPriceX = MARGIN.left + (curBinIdx + fracInBin) * barW;
    }
  }

  // Build path points
  const points = bins.map((b, i) => ({ x: xOf(i), y: yOf(getDepth(b)) }));

  const baseline = MARGIN.top + plotH;
  const buildPath = (pts) => {
    if (pts.length < 2) return "";
    return pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x},${p.y}`).join(" ");
  };

  const curvePath = buildPath(points);

  const areaPath = `M ${points[0].x},${baseline} L ${points[0].x},${points[0].y} ` +
    curvePath.slice(curvePath.indexOf("L")) +
    ` L ${points[points.length - 1].x},${baseline} Z`;

  const fmtAmt = (v) => {
    if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    if (v >= 1) return v.toFixed(1);
    if (v >= 0.01) return v.toFixed(2);
    return v.toFixed(4);
  };

  return (
    <div ref={containerRef} className="w-full h-full relative font-mono" onMouseLeave={() => setHoveredIdx(null)}>
      {dims.width > 0 && plotH > 0 && (
        <svg width={dims.width} height={dims.height}>
          <defs>
            <linearGradient id="liq-fill-left" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ec4899" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#ec4899" stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="liq-fill-right" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.05} />
            </linearGradient>
            <clipPath id="clip-plot">
              <rect x={MARGIN.left} y={MARGIN.top} width={plotW} height={plotH} />
            </clipPath>
            {curPriceX != null && (
              <>
                <clipPath id="clip-left">
                  <rect x={0} y={0} width={curPriceX} height={dims.height} />
                </clipPath>
                <clipPath id="clip-right">
                  <rect x={curPriceX} y={0} width={dims.width - curPriceX} height={dims.height} />
                </clipPath>
              </>
            )}
            <filter id="area-glow">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Horizontal grid lines */}
          {[0.25, 0.5, 0.75].map((frac) => (
            <line
              key={frac}
              x1={MARGIN.left}
              y1={MARGIN.top + plotH * (1 - frac)}
              x2={MARGIN.left + plotW}
              y2={MARGIN.top + plotH * (1 - frac)}
              stroke="#1e1e24"
              strokeDasharray="3 3"
            />
          ))}

          {/* Mountain area — dual color split */}
          <g clipPath="url(#clip-plot)">
          {curPriceX != null ? (
            <>
              <path d={areaPath} fill="url(#liq-fill-left)" clipPath="url(#clip-left)" />
              <path d={areaPath} fill="url(#liq-fill-right)" clipPath="url(#clip-right)" />
              <path d={curvePath} fill="none" stroke="#ec4899" strokeWidth={1.5} strokeOpacity={0.7} clipPath="url(#clip-left)" />
              <path d={curvePath} fill="none" stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.7} clipPath="url(#clip-right)" />
            </>
          ) : (
            <>
              <path d={areaPath} fill="url(#liq-fill-right)" />
              <path d={curvePath} fill="none" stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.7} />
            </>
          )}
          </g>

          {/* Hovered bar highlight */}
          {hoveredIdx !== null && (
            <rect
              x={MARGIN.left + hoveredIdx * barW}
              y={yOf(getDepth(bins[hoveredIdx]))}
              width={barW}
              height={baseline - yOf(getDepth(bins[hoveredIdx]))}
              fill={hoveredIdx <= curBinIdx ? "#ec4899" : "#22d3ee"}
              opacity={0.15}
            />
          )}

          {/* Invisible hover rects */}
          {bins.map((_, i) => (
            <rect
              key={`h-${i}`}
              x={MARGIN.left + i * barW}
              y={MARGIN.top}
              width={barW}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          ))}

          {/* Current price vertical line */}
          {curPriceX != null && (
            <>
              <line
                x1={curPriceX} y1={MARGIN.top}
                x2={curPriceX} y2={baseline}
                stroke="#ffffff" strokeWidth={1} strokeDasharray="4 3" strokeOpacity={0.5}
              />
              <text x={curPriceX} y={MARGIN.top - 3} textAnchor="middle" fill="#e4e4e7" fontSize={12} fontFamily="inherit">
                {currentPrice.toFixed(2)}
              </text>
            </>
          )}

          {/* Bottom axis line */}
          <line x1={MARGIN.left} y1={baseline} x2={MARGIN.left + plotW} y2={baseline} stroke="#3f3f46" />

          {/* X-axis labels (prices) */}
          {bins.map((bin, i) => {
            if (i % Math.max(1, Math.floor(bins.length / 6)) !== 0) return null;
            return (
              <text key={i} x={xOf(i)} y={baseline + 16} textAnchor="middle" fill="#71717a" fontSize={11} fontFamily="inherit">
                {Number(bin.price).toFixed(1)}
              </text>
            );
          })}

          {/* Y-axis labels (token amounts) */}
          {[0.25, 0.5, 0.75, 1].map((frac) => (
            <text key={frac} x={MARGIN.left - 6} y={MARGIN.top + plotH * (1 - frac) + 4} textAnchor="end" fill="#71717a" fontSize={11} fontFamily="inherit">
              {fmtAmt(maxDepth * frac)}
            </text>
          ))}
        </svg>
      )}

      {/* Tooltip */}
      {hoveredIdx !== null && dims.width > 0 && (() => {
        const bin = bins[hoveredIdx];
        const x = xOf(hoveredIdx);
        const tipY = yOf(getDepth(bin));
        const a0 = bin.amount0 ?? 0;
        const a1 = bin.amount1 ?? 0;
        return (
          <div
            className="absolute pointer-events-none bg-[#0a0a0a]/95 border border-zinc-800 px-3 py-2 text-xs font-mono shadow-xl z-10 rounded"
            style={{ left: Math.min(Math.max(x - 80, 4), dims.width - 180), top: Math.max(tipY - 70, 4) }}
          >
            <div className="text-zinc-400 mb-1.5">
              {Number(bin.priceFrom).toFixed(2)} &ndash; {Number(bin.priceTo).toFixed(2)}
            </div>
            <div className="flex items-center gap-2 mb-0.5">
              <span className="w-2 h-2 rounded-full bg-pink-500 inline-block" />
              <span className="text-zinc-400">Token 0:</span>
              <span className="text-white">{fmtAmt(a0)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-cyan-400 inline-block" />
              <span className="text-zinc-400">Token 1:</span>
              <span className="text-white">{fmtAmt(a1)}</span>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
