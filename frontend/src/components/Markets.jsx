import React, { useState, useEffect, useMemo } from 'react';
import { Loader2, TrendingUp, ArrowUpRight, Shield, Globe, Zap, Wallet, BarChart3, Clock, Activity, ChevronDown, Check } from 'lucide-react';
import { JsonRpcProvider, Contract, formatUnits } from 'ethers';
import useSWR from 'swr';
import axios from 'axios';
import RLDPerformanceChart from './RLDChart';
import SettingsButton from './SettingsButton';

const fetcher = (url) => axios.get(url).then((res) => res.data);

// --- HELPER FUNCTIONS ---
const getPastDate = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
};
const getToday = () => new Date().toISOString().split("T")[0];
const ASSETS = [
    {
        symbol: "USDC",
        name: "USD Coin",
        decimals: 6,
        debtToken: "0x72E95b8931767C79bA4EeE721354d6E99a61D004",
        icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
        color: "text-blue-400",
        protocol: "AAVE"
    },
    {
        symbol: "DAI",
        name: "Dai Stablecoin",
        decimals: 18,
        debtToken: "0xcF8d0c70c850859266f5C338b38F9D663181C314",
        icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
        color: "text-yellow-400",
        protocol: "AAVE"
    },
    {
        symbol: "USDT",
        name: "Tether USD",
        decimals: 6,
        debtToken: "0x6df1C1E379bC5a00a7b4C6e67A203333772f45A8",
        icon: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xdAC17F958D2ee523a2206206994597C13D831ec7/logo.png",
        color: "text-green-400",
        protocol: "AAVE"
    }
];

function MarketMetricBox({ label, value, sub, dimmed, Icon = Activity }) {
  return (
    <div
      className={`p-6 flex flex-col justify-between h-full min-h-[160px] ${
        dimmed ? "opacity-30" : ""
      }`}
    >
      <div className="text-[12px] text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label} <Icon size={15} className="opacity-90" />
      </div>
      <div>
        <div className="text-3xl font-light text-white mb-2 tracking-tight">
          {value}
        </div>
        <div className="text-[12px] text-gray-500 uppercase tracking-widest">
          {sub}
        </div>
      </div>
    </div>
  );
}

function FilterDropdown({ label, options, selected, onChange }) {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = React.useRef(null);

    // Close on click outside
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    const toggle = (option) => {
        const next = new Set(selected);
        if (next.has(option)) next.delete(option);
        else next.add(option);
        onChange(next);
    };

    const isAllSelected = selected.size === options.length;

    return (
        <div className="relative w-full" ref={dropdownRef}>
            <button 
                onClick={() => setIsOpen(!isOpen)}
                className={`
                    w-full flex items-center justify-between gap-2 px-4 py-3
                    border border-white/10 bg-white/[0.02] 
                    text-xs font-bold text-gray-400 
                    hover:text-white hover:border-white/20 hover:bg-white/[0.04]
                    transition-all duration-200 uppercase tracking-widest
                    ${isOpen ? 'border-white/20 bg-white/[0.04] text-white' : ''}
                `}
            >
                <div className="flex items-center gap-2 overflow-hidden">
                    <span>{label}</span>
                    <span className="text-gray-600 font-normal border-l border-white/10 pl-2 ml-1">
                        {isAllSelected ? "ALL" : selected.size}
                    </span>
                </div>
                <ChevronDown size={14} className={`transition-transform duration-200 ${isOpen ? 'rotate-180 text-cyan-500' : ''}`} />
            </button>
            
            {isOpen && (
                <div className="absolute top-full left-0 right-0 mt-2 bg-[#0f0f0f] border border-white/10 z-50 flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-100">
                    <div className="max-h-[300px] overflow-y-auto p-1 space-y-0.5 custom-scrollbar">
                        {/* SELECT ALL OPTION */}
                        <button
                            onClick={() => {
                                if (isAllSelected) onChange(new Set()); // Deselect all
                                else onChange(new Set(options)); // Select all
                            }}
                            className={`
                                w-full flex items-center gap-3 px-3 py-2.5 text-xs text-left uppercase tracking-widest transition-colors
                                ${isAllSelected ? 'bg-cyan-500/10 text-cyan-400' : 'text-gray-500 hover:bg-white/5 hover:text-gray-300'}
                                border-b border-white/5 mb-1
                            `}
                        >
                            <div className={`
                                w-3.5 h-3.5 border flex items-center justify-center transition-colors
                                ${isAllSelected ? 'bg-cyan-500 border-cyan-500' : 'border-white/20 group-hover:border-white/40'}
                            `}>
                                {isAllSelected && <Check size={10} className="text-black stroke-[3]" />}
                            </div>
                            ALL
                        </button>
                        
                        {options.map(opt => {
                            const isSelected = selected.has(opt);
                            return (
                                <button
                                    key={opt}
                                    onClick={() => toggle(opt)}
                                    className={`
                                        w-full flex items-center gap-3 px-3 py-2.5 text-xs text-left uppercase tracking-widest transition-colors
                                        ${isSelected ? 'bg-cyan-500/10 text-cyan-400' : 'text-gray-500 hover:bg-white/5 hover:text-gray-300'}
                                    `}
                                >
                                    <div className={`
                                        w-3.5 h-3.5 border flex items-center justify-center transition-colors
                                        ${isSelected ? 'bg-cyan-500 border-cyan-500' : 'border-white/20 group-hover:border-white/40'}
                                    `}>
                                        {isSelected && <Check size={10} className="text-black stroke-[3]" />}
                                    </div>
                                    {opt}
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}
export default function Markets() {
    const [marketData, setMarketData] = useState([]);
    const [loading, setLoading] = useState(true);
    
    // --- Chart State ---
    // --- Chart State ---
    const [tempStart, setTempStart] = useState(getPastDate(365)); 
    const [tempEnd, setTempEnd] = useState(getToday());
    const [appliedStart, setAppliedStart] = useState(getPastDate(365)); 
    const [appliedEnd, setAppliedEnd] = useState(getToday());
    const [activeRange, setActiveRange] = useState("1Y");
    const [resolution, setResolution] = useState("1D");
    
    // --- Filters State ---
    const [selectedProtocols, setSelectedProtocols] = useState(new Set(["AAVE", "MORPHO", "EULER", "FLUID"]));
    const [selectedAssets, setSelectedAssets] = useState(new Set(["USDC", "DAI", "USDT"]));

    // --- ACTIONS ---
    const handleApplyDate = () => {
        setAppliedStart(tempStart);
        setAppliedEnd(tempEnd);
        setActiveRange("CUSTOM");
    };

    const handleQuickRange = (days, label) => {
        const end = new Date();
        const start = new Date();
        start.setDate(end.getDate() - days);
        
        if (days <= 3) setResolution("RAW");
        else if (days <= 14) setResolution("1H");
        else if (days <= 90) setResolution("4H");
        else setResolution("1D");
        
        const startStr = start.toISOString().split("T")[0];
        const endStr = end.toISOString().split("T")[0];

        setTempStart(startStr);
        setTempEnd(endStr);
        setAppliedStart(startStr);
        setAppliedEnd(endStr);
        setActiveRange(label);
    };
    
    // --- Initial Data Fetch (Cards/Table) ---
    useEffect(() => {
        const fetchAllData = async () => {
            try {
                const provider = new JsonRpcProvider(import.meta.env.VITE_INFURA_RPC_URL);
                const ERC20_ABI = ["function totalSupply() view returns (uint256)"];

                const promises = ASSETS.map(async (asset) => {
                    let apy = 0;
                    try {
                        const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
                        const apiRes = await fetch(`${API_BASE}/rates?resolution=RAW&limit=1&symbol=${asset.symbol}`);
                        const apiData = await apiRes.json();
                        if (apiData && apiData.length > 0) apy = apiData[apiData.length - 1].apy || 0;
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
        const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
        return `${API_BASE}/rates?symbol=${symbol}&resolution=${resolution}&start_date=${appliedStart}&end_date=${appliedEnd}`;
    };

    const { data: usdcHistory } = useSWR(getHistoryUrl("USDC"), fetcher);
    const { data: daiHistory } = useSWR(getHistoryUrl("DAI"), fetcher);
    const { data: usdtHistory } = useSWR(getHistoryUrl("USDT"), fetcher);
    const { data: sofrHistory } = useSWR(getHistoryUrl("SOFR"), fetcher);
    
    const { data: ethPrices } = useSWR(
        () => {
            const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
            return `${API_BASE}/eth-prices?resolution=${resolution}&start_date=${appliedStart}&end_date=${appliedEnd}`;
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
            point[key] = val;
        };

        // 1. USDC
        usdcHistory.forEach(r => mergePoint(r.timestamp, "apy_usdc", r.apy));
        
        // 2. DAI
        if (daiHistory) daiHistory.forEach(r => mergePoint(r.timestamp, "apy_dai", r.apy));

        // 3. USDT
        if (usdtHistory) usdtHistory.forEach(r => mergePoint(r.timestamp, "apy_usdt", r.apy));
        
        // 4. SOFR (Risk Free Rate)
        if (sofrHistory) sofrHistory.forEach(r => mergePoint(r.timestamp, "apy_sofr", r.apy));

        // 5. ETH Price
        if (ethPrices) {
            ethPrices.forEach(p => mergePoint(p.timestamp, "ethPrice", p.price));
        } else {
             usdcHistory.forEach(r => {
                 if (r.eth_price) mergePoint(r.timestamp, "ethPrice", r.eth_price);
             });
        }

        const sortedData = Array.from(merged.values()).sort((a, b) => a.timestamp - b.timestamp);
        
        // Forward Fill SOFR (and potentially others) to ensure continuous lines/tooltips on weekends
        let lastSofr = null;
        return sortedData.map(point => {
            if (point.apy_sofr !== undefined && point.apy_sofr !== null) {
                lastSofr = point.apy_sofr;
            } else if (lastSofr !== null) {
                point.apy_sofr = lastSofr;
            }
            return point;
        });
    }, [usdcHistory, daiHistory, usdtHistory, sofrHistory, ethPrices, resolution]);


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



    // --- Legend / Series State ---
    const [hiddenSeries, setHiddenSeries] = useState(new Set());

    const SERIES_CONFIG = [
        { key: "apy_usdc", label: "USDC_Rate", name: "USDC Rate", color: "#22d3ee", bg: "bg-cyan-400" },
        { key: "apy_dai", label: "DAI_Rate", name: "DAI Rate", color: "#facc15", bg: "bg-yellow-400" },
        { key: "apy_usdt", label: "USDT_Rate", name: "USDT Rate", color: "#4ade80", bg: "bg-green-400" },
        { key: "apy_sofr", label: "SOFR_Rate", name: "SOFR (Risk Free)", color: "#c084fc", bg: "bg-purple-400" },
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
            <main className="max-w-7xl mx-auto px-6 pt-4 pb-12">
                
                {/* PAGE TITLE */}
                <div className="mb-8">
                    <h1 className="text-3xl font-medium tracking-tight text-white mb-2">
                        GLOBAL LIQUIDITY
                    </h1>
                    <p className="text-xs text-gray-500 uppercase tracking-widest">
                        Market Depth & Interest Rate Dynamics
                    </p>
                </div>

                {/* HERO STATS */}
                <div className="mb-6 border-y border-white/10 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/10">
                    <MarketMetricBox 
                        label="TOTAL_ACTIVE_DEBT" 
                        value={loading ? "..." : formatCurrency(stats.totalDebt)}
                        sub={<span className="text-green-500 flex items-center gap-1"><TrendingUp size={12} /> LIVE ON-CHAIN</span>}
                        Icon={Wallet}
                    />
                    <MarketMetricBox 
                        label="AVG_BORROW_RATE" 
                        value={loading ? "..." : `${stats.avgApy.toFixed(2)}%`}
                        sub="WEIGHTED AVERAGE (DEBT)"
                        Icon={Zap}
                    />
                    <MarketMetricBox 
                        label="TOP_MARKET" 
                        value={loading ? "..." : stats.topMarket.symbol}
                        sub={<span className="text-pink-500">{loading ? "0" : stats.dominance.toFixed(1)}% DOMINANCE</span>}
                        Icon={Globe}
                    />
                </div>

                {/* CONTROLS */}
                <div className="border-y border-white/10 grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0">
                  <ControlCell label="TIMEFRAME" className="pl-0">
                    {[
                      { l: "1W", d: 7 },
                      { l: "1M", d: 30 },
                      { l: "3M", d: 90 },
                      { l: "1Y", d: 365 },
                    ].map((btn) => (
                      <SettingsButton
                        key={btn.l}
                        onClick={() => handleQuickRange(btn.d, btn.l)}
                        isActive={activeRange === btn.l}
                        className="flex-1"
                      >
                        {btn.l}
                      </SettingsButton>
                    ))}
                  </ControlCell>
                  <ControlCell label="RESOLUTION">
                    {["RAW", "1H", "4H", "1D"].map((res) => (
                      <SettingsButton
                        key={res}
                        onClick={() => setResolution(res)}
                        isActive={resolution === res}
                        className="flex-1"
                      >
                        {res}
                      </SettingsButton>
                    ))}
                  </ControlCell>
                  <ControlCell label="CUSTOM_RANGE" className="pr-0">
                    <div className="flex items-center justify-between h-[30px] w-full gap-2">
                      <input
                        type="date"
                        value={tempStart}
                        onChange={(e) => setTempStart(e.target.value)}
                        className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-[38%] py-1 rounded-none"
                      />
                      <span className="text-gray-600 text-xs">-</span>
                      <input
                        type="date"
                        value={tempEnd}
                        onChange={(e) => setTempEnd(e.target.value)}
                        className="bg-transparent border-b border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-[38%] py-1 rounded-none"
                      />
                      <SettingsButton
                        onClick={handleApplyDate}
                        className="px-3 h-full flex items-center"
                      >
                        SET
                      </SettingsButton>
                    </div>
                  </ControlCell>
                </div>

                {/* CHART SECTION */}
                {/* CHART SECTION */}
                {/* CHART SECTION */}
                <div className="mb-6">
                   <div className="p-4 pl-0 pr-0">
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
                                   resolution={resolution}
                               />
                           )}
                       </div>
                   </div>
                </div>

                {/* TABLE HEADER */}


                
                {/* FILTERS */}
                <div className="mb-6 border-y border-white/10 grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/10">
                    <ControlCell label="PROTOCOL" className="pl-0">
                        <FilterDropdown 
                            label="Select Protocols"
                            options={["AAVE", "MORPHO", "EULER", "FLUID"]}
                            selected={selectedProtocols}
                            onChange={setSelectedProtocols}
                        />
                    </ControlCell>
                    <ControlCell label="ASSET" className="pr-0">
                        <FilterDropdown 
                            label="Select Assets"
                            options={["USDC", "DAI", "USDT"]}
                            selected={selectedAssets}
                            onChange={setSelectedAssets}
                        />
                    </ControlCell>
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
                                    <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-left">Asset</th>
                                    <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-center">Total Debt</th>
                                    <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-center">Borrow APY</th>
                                    <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-center">Protocol</th>
                                    <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-center">Network</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/5">
                                {marketData
                                    .filter(m => m.protocol && selectedProtocols.has(m.protocol)) 
                                    .filter(m => selectedAssets.has(m.symbol))
                                    .map((m) => (
                                    <tr key={m.symbol} className="hover:bg-white/[0.03] transition-all duration-300 group cursor-default">

                                        <td className="p-5">
                                            <div className="flex items-center gap-4">
                                                <div className="relative">
                                                    <div className="w-10 h-10 rounded-full bg-[#151515] border border-white/10 flex items-center justify-center p-2 group-hover:border-white/30 transition-colors">
                                                         <img src={m.icon} alt={m.symbol} className="w-full h-full object-contain rounded-full" />
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
                                        <td className="p-5 text-center">
                                            <div className="text-xs font-mono font-bold tracking-widest text-white">
                                                {formatCurrency(m.debt)}
                                            </div>
                                        </td>
                                        <td className="p-5 text-center">
                                             <div className="flex flex-col items-center">
                                                <div className="text-xs font-mono font-bold tracking-widest text-cyan-400">
                                                    {m.apy.toFixed(2)}%
                                                </div>
                                             </div>
                                        </td>
                                        <td className="p-5 text-center">
                                            <div className="flex items-center justify-center gap-3">
                                                 <img 
                                                    src="https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9/logo.png" 
                                                    alt={m.protocol} 
                                                    className="w-5 h-5 object-contain"
                                                />
                                                <span className="text-xs uppercase tracking-widest font-bold text-white">
                                                    {m.protocol}
                                                </span>
                                            </div>
                                        </td>
                                        <td className="p-5 text-center">
                                            <span className="text-xs uppercase tracking-widest font-bold text-white">
                                                ETHEREUM
                                            </span>
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

function ControlCell({ label, children, className = "" }) {
  return (
    <div className={`p-4 flex flex-col gap-3 ${className}`}>
      <span className="text-[11px] text-gray-500 uppercase tracking-[0.2em] font-bold">
        {label}
      </span>
      <div className="flex items-center w-full">{children}</div>
    </div>
  );
}
