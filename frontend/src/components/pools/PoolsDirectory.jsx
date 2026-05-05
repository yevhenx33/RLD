import React, { useState, useMemo } from "react";
import useSWR from "swr";
import { useNavigate } from "react-router-dom";
import {
  Droplets,
  ChevronDown,
  ChevronUp,
  ArrowUpDown,
  Loader2,
} from "lucide-react";
import { SIM_GRAPHQL_URL } from "../../api/endpoints";
import { postGraphQL } from "../../api/graphqlClient";
import { REFRESH_INTERVALS } from "../../config/refreshIntervals";
import {
  POOLS_QUERY,
  buildPoolsDirectoryRows,
  formatUSD,
} from "./poolsDirectoryData";

const fetchPools = ([url]) => postGraphQL(url, { query: POOLS_QUERY });

export default function PoolsDirectory() {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState("tvl");
  const [sortDir, setSortDir] = useState("desc");

  const {
    data,
    error,
    isLoading,
  } = useSWR([SIM_GRAPHQL_URL, "pools-directory"], fetchPools, {
    refreshInterval: REFRESH_INTERVALS.SIMULATION_SNAPSHOT_MS,
    revalidateOnFocus: false,
    dedupingInterval: REFRESH_INTERVALS.FAST_DEDUPE_MS,
    keepPreviousData: true,
  });

  const connected = !error;

  const pools = useMemo(() => buildPoolsDirectoryRows(data), [data]);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const getSortIcon = (col) => {
    if (sortKey !== col)
      return <ArrowUpDown size={10} className="opacity-30" />;
    return sortDir === "desc" ? (
      <ChevronDown size={10} className="text-cyan-400" />
    ) : (
      <ChevronUp size={10} className="text-cyan-400" />
    );
  };

  const filteredPools = useMemo(() => {
    const list = [...pools];
    list.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string")
        return sortDir === "asc"
          ? av.localeCompare(bv)
          : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return list;
  }, [pools, sortKey, sortDir]);

  if (isLoading && pools.length === 0) {
    return (
      <div className="min-h-screen bg-[#050505] text-gray-300 font-mono flex items-center justify-center">
        <Loader2 className="animate-spin text-gray-700" size={24} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        {/* Header Metrics */}
        <div className="border border-white/10 grid grid-cols-1 lg:grid-cols-12">
          {/* Branding */}
          <div className="lg:col-span-5 flex flex-col justify-center p-6 border-b lg:border-b-0 lg:border-r border-white/10 min-h-[140px]">
            <div className="flex items-center gap-3 mb-2">
              <Droplets size={18} className="text-cyan-400" />
              <h1 className="text-2xl font-medium tracking-tight">
                Liquidity Pools
              </h1>
            </div>
            <p className="text-sm text-gray-500 tracking-widest uppercase">
              {pools.length} active pool{pools.length !== 1 ? "s" : ""} - Uniswap V4
              {!connected && (
                <span className="ml-2 text-yellow-600">- Disconnected</span>
              )}
            </p>
          </div>

          {/* Metrics */}
          <div className="lg:col-span-7 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
            {/* TVL */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Total TVL
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {formatUSD(pools.reduce((s, p) => s + p.tvl, 0))}
              </div>
            </div>

            {/* Trade Volume */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Trade Volume 24H
              </div>
              <div className="text-2xl font-light tracking-tight text-white">
                {formatUSD(pools.reduce((s, p) => s + p.volume24h, 0))}
              </div>
            </div>

            {/* Fees 24H */}
            <div className="p-6 flex flex-col justify-center">
              <div className="text-sm text-gray-500 uppercase tracking-widest mb-2">
                Fees 24H
              </div>
              <div className="text-2xl font-light tracking-tight text-green-400">
                {formatUSD(pools.reduce((s, p) => s + p.fees24h, 0))}
              </div>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="border border-white/10">
          {/* Desktop Table Header (lg+) */}
          <div className="hidden lg:grid grid-cols-12 gap-4 px-6 py-3 text-sm text-gray-500 uppercase tracking-widest border-b border-white/5 bg-[#0a0a0a]">
            <button
              onClick={() => toggleSort("pair")}
              className="col-span-3 relative flex items-center gap-1.5 text-left hover:text-white transition-colors"
            >
              Pool {getSortIcon("pair")}
            </button>
            <button
              onClick={() => toggleSort("tvl")}
              className="col-span-2 relative text-center hover:text-white transition-colors"
            >
              TVL{" "}
              <span className="absolute ml-1 top-1/2 -translate-y-1/2">
                {getSortIcon("tvl")}
              </span>
            </button>
            <button
              onClick={() => toggleSort("volume24h")}
              className="col-span-2 relative text-center hover:text-white transition-colors"
            >
              Volume 24H{" "}
              <span className="absolute ml-1 top-1/2 -translate-y-1/2">
                {getSortIcon("volume24h")}
              </span>
            </button>
            <button
              onClick={() => toggleSort("fees24h")}
              className="col-span-2 relative text-center hover:text-white transition-colors"
            >
              Fees 24H{" "}
              <span className="absolute ml-1 top-1/2 -translate-y-1/2">
                {getSortIcon("fees24h")}
              </span>
            </button>
            <button
              onClick={() => toggleSort("apr7d")}
              className="col-span-1 relative text-center hover:text-white transition-colors"
            >
              APR 7D{" "}
              <span className="absolute ml-1 top-1/2 -translate-y-1/2">
                {getSortIcon("apr7d")}
              </span>
            </button>
            <button
              onClick={() => toggleSort("apr30d")}
              className="col-span-2 relative text-center hover:text-white transition-colors"
            >
              APR 30D{" "}
              <span className="absolute ml-1 top-1/2 -translate-y-1/2">
                {getSortIcon("apr30d")}
              </span>
            </button>
          </div>

          {/* Table Rows */}
          {filteredPools.length === 0 ? (
            <div className="px-6 py-12 text-center text-gray-600 text-sm uppercase tracking-widest">
              {connected
                ? "No pools found"
                : "Connecting to indexer..."}
            </div>
          ) : (
            filteredPools.map((pool) => (
              <div
                key={pool.id}
                onClick={() => navigate(`/markets/pools/${pool.address}`)}
                className="border-b border-white/5 last:border-b-0 cursor-pointer group hover:bg-white/[0.02] transition-colors"
              >
                {/* Desktop Row (lg+) */}
                <div className="hidden lg:grid grid-cols-12 gap-4 px-6 py-4 items-center">
                  {/* Pool */}
                  <div className="col-span-3">
                    <div className="flex items-center gap-3">
                      <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_6px_rgba(6,182,212,0.4)]" />
                      <div>
                        <div className="text-sm font-mono text-white group-hover:text-cyan-400 transition-colors">
                          {pool.pair}
                        </div>
                      </div>
                    </div>
                  </div>
                  {/* TVL */}
                  <div className="col-span-2 text-sm font-mono text-white text-center">
                    {formatUSD(pool.tvl)}
                  </div>
                  {/* Volume 24H */}
                  <div className="col-span-2 text-sm font-mono text-white text-center">
                    {formatUSD(pool.volume24h)}
                  </div>
                  {/* Fees 24H */}
                  <div className="col-span-2 text-sm font-mono text-white text-center">
                    {formatUSD(pool.fees24h)}
                  </div>
                  {/* APR 7D */}
                  <div className="col-span-1 text-sm font-mono text-green-400 text-center">
                    {pool.apr7d.toFixed(1)}%
                  </div>
                  {/* APR 30D */}
                  <div className="col-span-2 text-sm font-mono text-green-400 text-center">
                    {pool.apr30d.toFixed(1)}%
                  </div>
                </div>

                {/* Mobile Card (<lg) */}
                <div className="lg:hidden flex flex-col">
                  {/* Card Header: cyan accent bar */}
                  <div className="flex items-center justify-between px-4 py-3 bg-cyan-500/[0.06] border-b border-cyan-500/10">
                    <div className="text-base font-mono text-cyan-400 font-bold tracking-tight">
                      {pool.pair}
                    </div>
                  </div>
                  {/* Card Metrics */}
                  <div className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-4">
                    <div className="flex flex-col">
                      <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">TVL</span>
                      <span className="text-base font-mono text-white">{formatUSD(pool.tvl)}</span>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">Volume</span>
                      <span className="text-base font-mono text-white">{formatUSD(pool.volume24h)}</span>
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">Fees 24H</span>
                      <span className="text-base font-mono text-white">{formatUSD(pool.fees24h)}</span>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-[10px] text-gray-600 uppercase tracking-widest mb-1">APR 7D</span>
                      <span className="text-base font-mono text-green-400">{pool.apr7d.toFixed(1)}%</span>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}

          {/* Footer */}
          <div className="px-4 md:px-6 py-3 border-t border-white/5 bg-[#0a0a0a] flex justify-between items-center text-[10px] uppercase tracking-widest text-gray-600">
            <span>Showing {pools.length} Pool{pools.length !== 1 ? "s" : ""}</span>
            <span className="flex items-center gap-1">
              Data provided by <span className="text-white ml-1">RLD Protocol</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
