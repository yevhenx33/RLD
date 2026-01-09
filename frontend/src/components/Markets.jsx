import React, { useState, useEffect } from 'react';
import Header from './Header';
import { Loader2, Activity, Database, Network, TrendingUp, TrendingDown, ArrowRight } from 'lucide-react';
import { JsonRpcProvider, Contract, formatUnits } from 'ethers';

const ASSETS = [
    {
        symbol: "USDC",
        name: "USD Coin",
        decimals: 6,
        debtToken: "0x72E95b8931767C79bA4EeE721354d6E99a61D004",
        icon: "https://icons.llama.fi/usdc.png"
    },
    {
        symbol: "DAI",
        name: "Dai Stablecoin",
        decimals: 18,
        debtToken: "0xcF8d0c70c850859266f5C338b38F9D663181C314",
        icon: "https://icons.llama.fi/dai.png"
    },
    {
        symbol: "USDT",
        name: "Tether USD",
        decimals: 6,
        debtToken: "0x6df1C1E379bC5a00a7b4C6e67A203333772f45A8",
        icon: "https://icons.llama.fi/usdt.png"
    }
];

export default function Markets() {
    const [marketData, setMarketData] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchAllData = async () => {
            try {
                const provider = new JsonRpcProvider("https://eth.llamarpc.com");
                const ERC20_ABI = ["function totalSupply() view returns (uint256)"];

                const promises = ASSETS.map(async (asset) => {
                    // 1. Fetch APY from Backend
                    let apy = 0;
                    try {
                        const apiRes = await fetch(`http://localhost:8000/rates?resolution=RAW&limit=1&symbol=${asset.symbol}`);
                        const apiData = await apiRes.json();
                        if (apiData && apiData.length > 0) {
                            apy = apiData[apiData.length - 1].apy;
                        }
                    } catch (e) {
                        console.error(`Failed to fetch APY for ${asset.symbol}`, e);
                    }

                    // 2. Fetch On-Chain Debt
                    let debt = 0;
                    try {
                        const debtContract = new Contract(asset.debtToken, ERC20_ABI, provider);
                        const rawDebt = await debtContract.totalSupply();
                        debt = parseFloat(formatUnits(rawDebt, asset.decimals));
                    } catch (e) {
                         console.error(`Failed to fetch Debt for ${asset.symbol}`, e);
                    }

                    return {
                        ...asset,
                        apy,
                        debt
                    };
                });

                const results = await Promise.all(promises);
                // Sort by Debt Descending
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

    const formatCurrency = (value) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 0
        }).format(value);
    };

    return (
        <div className="min-h-screen bg-[#050505] text-gray-300 font-mono selection:bg-pink-500/30">
            <Header />
            
            <main className="max-w-7xl mx-auto px-6 py-12">
                <div className="mb-12 border-b border-white/10 pb-6 flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl font-bold text-white tracking-widest uppercase mb-2">
                            AAVE V3 <span className="text-pink-500">MARKETS</span>
                        </h1>
                        <div className="flex items-center gap-4 text-xs tracking-widest text-gray-500 uppercase">
                            <span>Ethereum Mainnet</span>
                            <span>•</span>
                            <span className="text-green-500 flex items-center gap-1">
                                <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse"></span>
                                Live Data
                            </span>
                        </div>
                    </div>
                </div>

                {loading ? (
                    <div className="h-64 w-full flex flex-col items-center justify-center gap-4 border border-white/10 bg-[#080808] rounded-sm">
                        <Loader2 className="w-10 h-10 text-pink-500 animate-spin" />
                        <span className="text-xs tracking-widest uppercase text-gray-500">Syncing Multi-Asset Data...</span>
                    </div>
                ) : (
                    <div className="overflow-x-auto border border-white/10 bg-[#080808]">
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="border-b border-white/10 text-xs uppercase tracking-widest text-gray-500">
                                    <th className="p-6 font-medium">Asset</th>
                                    <th className="p-6 font-medium">Chain</th>
                                    <th className="p-6 font-medium text-right">Total Outstanding Debt</th>
                                    <th className="p-6 font-medium text-right">Borrow APY</th>
                                    <th className="p-6 font-medium text-right">Status</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/5">
                                {marketData.map((m) => (
                                    <tr key={m.symbol} className="hover:bg-white/[0.02] transition-colors group">
                                        <td className="p-6">
                                            <div className="flex items-center gap-4">
                                                <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center overflow-hidden">
                                                     <img src={m.icon} alt={m.symbol} className="w-full h-full object-cover" />
                                                </div>
                                                <div>
                                                    <div className="text-lg font-bold text-white tracking-tight">{m.symbol}</div>
                                                    <div className="text-[10px] text-gray-600 uppercase tracking-widest">{m.name}</div>
                                                </div>
                                            </div>
                                        </td>
                                        <td className="p-6">
                                            <div className="flex items-center gap-2">
                                                <Network size={14} className="text-gray-600" />
                                                <span className="text-sm text-gray-400">Ethereum</span>
                                            </div>
                                        </td>
                                        <td className="p-6 text-right">
                                            <div className="text-xl font-mono text-white tracking-tight">
                                                {formatCurrency(m.debt)}
                                            </div>
                                            <div className="text-[10px] text-gray-600 uppercase tracking-widest mt-1">
                                                On-Chain
                                            </div>
                                        </td>
                                        <td className="p-6 text-right">
                                             <div className="flex flex-col items-end">
                                                <div className="text-xl font-mono text-cyan-400 tracking-tight font-bold">
                                                    {m.apy.toFixed(2)}%
                                                </div>
                                                <div className="text-[10px] text-gray-600 uppercase tracking-widest mt-1">
                                                    Variable Rate
                                                </div>
                                             </div>
                                        </td>
                                        <td className="p-6 text-right">
                                            <div className="flex justify-end">
                                                <a 
                                                    href={`https://app.aave.com/reserve-overview/?underlyingAsset=${m.debtToken}&marketName=proto_mainnet_v3`} 
                                                    target="_blank" 
                                                    rel="noreferrer"
                                                    className="flex items-center gap-2 text-xs uppercase tracking-widest text-gray-500 hover:text-white transition-colors group-hover:underline underline-offset-4"
                                                >
                                                    View <ArrowRight size={12} />
                                                </a>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </main>
        </div>
    );
}
