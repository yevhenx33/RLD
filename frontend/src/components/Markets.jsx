import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, TrendingUp, ArrowUpRight, Shield, Globe, Zap, Wallet, BarChart3, Clock } from 'lucide-react';
import { JsonRpcProvider, Contract, formatUnits } from 'ethers';
import useSWR from 'swr';
import axios from 'axios';
import RLDPerformanceChart from './RLDChart';
import SettingsButton from './SettingsButton';

const fetcher = (url) => axios.get(url).then((res) => res.data);

const ASSETS = [
    {
        symbol: "USDC",
        name: "USD Coin",
        decimals: 6,
        debtToken: "0x72E95b8931767C79bA4EeE721354d6E99a61D004",
        icon: "https://icons.llama.fi/usdc.png",
        color: "text-blue-400"
    },
    {
        symbol: "DAI",
        name: "Dai Stablecoin",
        decimals: 18,
        debtToken: "0xcF8d0c70c850859266f5C338b38F9D663181C314",
        icon: "https://icons.llama.fi/dai.png",
        color: "text-yellow-400"
    },
    {
        symbol: "USDT",
        name: "Tether USD",
        decimals: 6,
        debtToken: "0x6df1C1E379bC5a00a7b4C6e67A203333772f45A8",
        icon: "https://icons.llama.fi/usdt.png",
        color: "text-green-400"
    }
];

export default function Markets() {
    const [marketData, setMarketData] = useState([]);
    const [loading, setLoading] = useState(true);
    
    // --- Chart State ---
    const [activeRange, setActiveRange] = useState("1M");
    const [resolution, setResolution] = useState("4H");
    
    // --- Initial Data Fetch (Cards/Table) ---
    useEffect(() => {
        const fetchAllData = async () => {
            try {
                const provider = new JsonRpcProvider("https://mainnet.infura.io/v3/34f11149110c484d9c9c56dde2e8aeda");
                const ERC20_ABI = ["function totalSupply() view returns (uint256)"];

                const promises = ASSETS.map(async (asset) => {
                    let apy = 0;
                    try {
                        const apiRes = await fetch(`http://localhost:8000/rates?resolution=RAW&limit=1&symbol=${asset.symbol}`);
                        const apiData = await apiRes.json();
                        if (apiData && apiData.length > 0) apy = apiData[apiData.length - 1].apy;
                    } catch (e) {
                         console.error(`Failed to fetch APY for ${asset.symbol}`, e);
                    }

                    let debt = 0;
                    try {
                        const debtContract = new Contract(asset.debtToken, ERC20_ABI, provider);
                        const rawDebt = await debtContract.totalSupply();
                        debt = parseFloat(formatUnits(rawDebt, asset.decimals));
                    } catch (e) {
                         console.error(`Failed to fetch Debt for ${asset.symbol}`, e);
                    }

                    return { ...asset, apy, debt };
                });

                const results = await Promise.all(promises);
                results.sort((a, b) => b.debt - a.debt);
                
                setMarketData(results);
                setLoading(false);

            } catch (err) {
                console.error("Markets Fetch Error:", err);
                setLoading(false);
            }
        };

        fetchAllData();
    }, []);

    // --- Chart Data Fetching (USDC History) ---
    const getHistoryUrl = (symbol) => {
        const end = new Date();
        const start = new Date();
        let days = 30;
        if (activeRange === "1W") days = 7;
        if (activeRange === "3M") days = 90;
        if (activeRange === "1Y") days = 365;

        start.setDate(end.getDate() - days);
        const startStr = start.toISOString().split("T")[0];
        return `http://localhost:8000/rates?symbol=${symbol}&resolution=${resolution}&start_date=${startStr}`;
    };

    const { data: usdcHistory } = useSWR(getHistoryUrl("USDC"), fetcher);
    const { data: daiHistory } = useSWR(getHistoryUrl("DAI"), fetcher);
    const { data: usdtHistory } = useSWR(getHistoryUrl("USDT"), fetcher);
    
    const { data: ethPrices } = useSWR(
        () => {
            const end = new Date();
            const start = new Date();
            let days = 30;
            if (activeRange === "1W") days = 7;
            if (activeRange === "3M") days = 90;
            if (activeRange === "1Y") days = 365;
            start.setDate(end.getDate() - days);
            const startStr = start.toISOString().split("T")[0];
            return `http://localhost:8000/eth-prices?resolution=${resolution}&start_date=${startStr}`;
        },
        fetcher
    );

    // Merge Data for Chart
    const chartData = useMemo(() => {
        if (!usdcHistory || usdcHistory.length === 0) return [];
        
        // Define bucket size based on resolution
        const getBucket = (ts) => {
            let seconds = 3600; // Default 1H
            if (resolution === "4H") seconds = 14400;
            if (resolution === "1D") seconds = 86400;
            if (resolution === "1W") seconds = 604800;
            return Math.floor(ts / seconds) * seconds;
        };

        const merged = new Map();

        const mergePoint = (ts, key, val) => {
            const bucket = getBucket(ts);
            if (!merged.has(bucket)) {
                merged.set(bucket, { timestamp: bucket });
            }
            const point = merged.get(bucket);
            // If multiple points fall in same bucket, take the latest (or avg?) 
            // Simple overwrite is fine for "latest in bucket"
            point[key] = val;
        };

        // 1. USDC
        usdcHistory.forEach(r => mergePoint(r.timestamp, "apy_usdc", r.apy));
        
        // 2. DAI
        if (daiHistory) daiHistory.forEach(r => mergePoint(r.timestamp, "apy_dai", r.apy));

        // 3. USDT
        if (usdtHistory) usdtHistory.forEach(r => mergePoint(r.timestamp, "apy_usdt", r.apy));

        // 4. ETH Price
        // Prefer price from ethPrices endpoint, fallback to USDC object if present
        if (ethPrices) {
            ethPrices.forEach(p => mergePoint(p.timestamp, "ethPrice", p.price));
        } else {
             usdcHistory.forEach(r => {
                 if (r.eth_price) mergePoint(r.timestamp, "ethPrice", r.eth_price);
             });
        }

        return Array.from(merged.values()).sort((a, b) => a.timestamp - b.timestamp);
    }, [usdcHistory, daiHistory, usdtHistory, ethPrices, resolution]);


    const stats = useMemo(() => {
        const totalDebt = marketData.reduce((acc, curr) => acc + curr.debt, 0);
        
        const weightedSum = marketData.reduce((acc, curr) => acc + (curr.apy * curr.debt), 0);
        const avgApy = totalDebt > 0 ? weightedSum / totalDebt : 0;
        
        const topMarket = marketData.reduce((prev, current) => (prev.debt > current.debt) ? prev : current, { symbol: '-', debt: 0 });
        const dominance = totalDebt > 0 ? (topMarket.debt / totalDebt) * 100 : 0;

        return { totalDebt, avgApy, topMarket, dominance };
    }, [marketData]);

    const formatCurrency = (value) => {
        if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
        if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
    };

    const handleRangeChange = (range) => {
        setActiveRange(range);
        if (range === "1W") setResolution("1H");
        else if (range === "1M") setResolution("4H");
        else if (range === "3M") setResolution("4H");
        else setResolution("1D");
    };

    // --- Legend / Series State ---
    const [hiddenSeries, setHiddenSeries] = useState(new Set());

    const SERIES_CONFIG = [
        { key: "apy_usdc", label: "USDC_Rate", name: "USDC Rate", color: "#22d3ee", bg: "bg-cyan-400" },
        { key: "apy_dai", label: "DAI_Rate", name: "DAI Rate", color: "#facc15", bg: "bg-yellow-400" },
        { key: "apy_usdt", label: "USDT_Rate", name: "USDT Rate", color: "#4ade80", bg: "bg-green-400" },
        { key: "ethPrice", label: "ETH_Price", name: "ETH Price", color: "#a1a1aa", bg: "bg-zinc-400", yAxisId: "right" }
    ];

    const toggleSeries = (key) => {
        const next = new Set(hiddenSeries);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        setHiddenSeries(next);
    };

    const activeAreas = useMemo(() => {
        return SERIES_CONFIG.filter(s => !hiddenSeries.has(s.key)).map(s => ({
            key: s.key,
            name: s.name,
            color: s.color,
            yAxisId: s.yAxisId
        }));
    }, [hiddenSeries]);

    return (
        <div className="min-h-screen bg-[#050505] text-gray-300 font-mono selection:bg-pink-500/30">
            <main className="max-w-7xl mx-auto px-6 py-12">
                
                {/* HERO STATS */}
                <div className="mb-12 grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div className="border border-white/10 bg-[#0a0a0a] p-6 relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
                            <Wallet size={48} />
                        </div>
                        <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-2 font-bold">Total Active Debt</div>
                        <div className="text-3xl font-bold text-white tracking-tight">
                            {loading ? "..." : formatCurrency(stats.totalDebt)}
                        </div>
                        <div className="text-xs text-green-500 mt-2 flex items-center gap-1">
                            <TrendingUp size={12} /> Live On-Chain
                        </div>
                    </div>

                    <div className="border border-white/10 bg-[#0a0a0a] p-6 relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
                            <BarChart3 size={48} />
                        </div>
                        <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-2 font-bold">Avg. Borrow Rate</div>
                        <div className="text-3xl font-bold text-cyan-400 tracking-tight">
                            {loading ? "..." : `${stats.avgApy.toFixed(2)}%`}
                        </div>
                        <div className="text-xs text-gray-500 mt-2">
                             Weighted Average (Debt)
                        </div>
                    </div>

                    <div className="border border-white/10 bg-[#0a0a0a] p-6 relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
                            <Shield size={48} />
                        </div>
                        <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-2 font-bold">Top Market</div>
                        <div className="text-3xl font-bold text-white tracking-tight flex items-center gap-2">
                            {loading ? "..." : stats.topMarket.symbol}
                        </div>
                        <div className="text-xs text-pink-500 mt-2 flex items-center gap-1">
                             {loading ? "0" : stats.dominance.toFixed(1)}% Dominance
                        </div>
                    </div>
                </div>

                {/* CHART SECTION */}
                <div className="mb-12">
                   <div className="bg-[#0a0a0a] border border-white/10 p-4">
                       <div className="flex justify-between items-end mb-4 px-1">
                           <div className="flex gap-8">
                               {SERIES_CONFIG.map(series => (
                                   <div 
                                       key={series.key}
                                       onClick={() => toggleSeries(series.key)}
                                       className={`flex items-center gap-2 cursor-pointer transition-all ${hiddenSeries.has(series.key) ? 'opacity-50 line-through' : 'opacity-100 hover:opacity-80'}`}
                                   >
                                       <div className={`w-2 h-2 ${series.bg} rounded-none`}></div> 
                                       <span className="text-[11px] uppercase tracking-widest text-[#e0e0e0]">
                                           {series.label}
                                       </span>
                                   </div>
                               ))}
                           </div>
                           <div className="flex gap-1">
                               {["1W", "1M", "3M", "1Y"].map(range => (
                                   <SettingsButton 
                                       key={range} 
                                       isActive={activeRange === range} 
                                       onClick={() => handleRangeChange(range)}
                                       className="w-12 h-6 text-[10px]"
                                   >
                                       {range}
                                   </SettingsButton>
                               ))}
                           </div>
                       </div>
                       
                       <div className="h-[350px] w-full">
                           {!usdcHistory ? (
                               <div className="h-full flex items-center justify-center">
                                   <Loader2 className="animate-spin text-gray-700" />
                               </div>
                           ) : (
                               <RLDPerformanceChart 
                                   data={chartData}
                                   areas={activeAreas} 
                               />
                           )}
                       </div>
                   </div>
                </div>

                {/* TABLE HEADER */}
                <div className="mb-6 flex justify-between items-end">
                    <div>
                        <h2 className="text-xl font-light text-white tracking-wide uppercase mb-1">
                            Active Markets
                        </h2>
                        <div className="text-[10px] uppercase tracking-widest text-gray-500">
                            Real-time Lending Opportunities
                        </div>
                    </div>
                </div>

                {/* MAIN TABLE */}
                <div className="border border-white/10 bg-[#0a0a0a] relative">
                    {loading && (
                        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm z-10 flex flex-col items-center justify-center">
                             <Loader2 className="w-8 h-8 text-cyan-500 animate-spin mb-2" />
                             <span className="text-[10px] uppercase tracking-widest text-white">Syncing Data...</span>
                        </div>
                    )}
                    
                    <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="border-b border-white/10 bg-white/[0.02]">
                                    <th className="p-5 text-[10px] uppercase tracking-widest text-gray-500 font-bold">Asset</th>
                                    <th className="p-5 text-[10px] uppercase tracking-widest text-gray-500 font-bold">Network</th>
                                    <th className="p-5 text-[10px] uppercase tracking-widest text-gray-500 font-bold text-right">Total Debt</th>
                                    <th className="p-5 text-[10px] uppercase tracking-widest text-gray-500 font-bold text-right">Borrow APY</th>
                                    <th className="p-5 text-[10px] uppercase tracking-widest text-gray-500 font-bold text-right">Action</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/5">
                                {marketData.map((m) => (
                                    <tr key={m.symbol} className="hover:bg-white/[0.03] transition-all duration-300 group cursor-default">
                                        <td className="p-5">
                                            <div className="flex items-center gap-4">
                                                <div className="relative">
                                                    <div className="w-10 h-10 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-2 group-hover:border-white/30 transition-colors">
                                                         <img src={m.icon} alt={m.symbol} className="w-full h-full object-contain" />
                                                    </div>
                                                    <div className="absolute -bottom-1 -right-1 w-4 h-4 bg-[#0a0a0a] rounded-full flex items-center justify-center border border-white/10">
                                                        <Zap size={8} className="text-yellow-500" fill="currentColor" />
                                                    </div>
                                                </div>
                                                <div>
                                                    <div className="text-base font-bold text-white tracking-tight flex items-center gap-2">
                                                        {m.symbol}

                                                    </div>
                                                    <div className="text-[10px] text-gray-600 uppercase tracking-widest font-bold">{m.name}</div>
                                                </div>
                                            </div>
                                        </td>
                                        <td className="p-5">
                                            <div className="flex items-center gap-2">
                                                <div className="w-2 h-2 rounded-full bg-slate-700"></div>
                                                <span className="text-xs text-gray-400 font-medium tracking-wide">Ethereum</span>
                                            </div>
                                        </td>
                                        <td className="p-5 text-right">
                                            <div className="text-lg font-mono font-medium text-white">
                                                {formatCurrency(m.debt)}
                                            </div>

                                        </td>
                                        <td className="p-5 text-right">
                                             <div className="flex flex-col items-end">
                                                <div className="text-lg font-mono font-bold text-cyan-400">
                                                    {m.apy.toFixed(2)}%
                                                </div>
                                                <div className="text-[10px] text-gray-600 uppercase tracking-widest mt-0.5">
                                                    Variable
                                                </div>
                                             </div>
                                        </td>
                                        <td className="p-5 text-right">
                                            <div className="flex justify-end">
                                                <a 
                                                    href={`https://app.aave.com/reserve-overview/?underlyingAsset=${m.debtToken}&marketName=proto_mainnet_v3`} 
                                                    target="_blank" 
                                                    rel="noreferrer"
                                                    className="
                                                        flex items-center gap-2 px-4 py-2 
                                                        border border-white/10 bg-white/[0.02] 
                                                        text-xs uppercase tracking-widest font-bold text-gray-400 
                                                        hover:text-white hover:border-cyan-500/50 hover:bg-cyan-500/10 
                                                        transition-all duration-300
                                                    "
                                                >
                                                    Manage <ArrowUpRight size={12} />
                                                </a>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        
                        {/* Empty State / Footer */}
                        {!loading && marketData.length > 0 && (
                            <div className="p-4 border-t border-white/5 bg-[#0d0d0d] flex justify-between items-center text-[10px] uppercase tracking-widest text-gray-600">
                                <span>Showing {marketData.length} Assets</span>
                                <span className="flex items-center gap-1">Data provided by <span className="text-white">Aave V3</span></span>
                            </div>
                        )}
                    </div>
                </div>
            </main>
        </div>
    );
}
