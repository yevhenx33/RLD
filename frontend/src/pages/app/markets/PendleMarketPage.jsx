import React, { useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import useSWR from "swr";
import { ArrowLeft, Loader2 } from "lucide-react";
import { API_GRAPHQL_URL } from "../../../api/endpoints";
import { apiGraphQL } from "../../../api/apiClient";
import { PENDLE_MARKET_QUERY } from "../../../api/apiQueries";
import { queryKeys } from "../../../api/queryKeys";
import { REFRESH_INTERVALS } from "../../../config/refreshIntervals";

function normalizeSearch(value) {
  const cleaned = String(value || "").trim();
  if (!cleaned) return "";
  return cleaned.startsWith("0x") ? cleaned.toLowerCase() : `0x${cleaned.toLowerCase()}`;
}

function formatUsd(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return "$0.0000";
  if (numeric >= 1) return `$${numeric.toFixed(4)}`;
  return `$${numeric.toPrecision(4)}`;
}

function formatDate(ts) {
  const numeric = Number(ts || 0);
  if (!numeric) return "n/a";
  return new Date(numeric * 1000).toISOString().slice(0, 10);
}

export default function PendleMarketPage() {
  const { marketId } = useParams();
  const navigate = useNavigate();
  const search = normalizeSearch(marketId);

  const { data, error, isLoading } = useSWR(
    queryKeys.apiPendleMarketPage(API_GRAPHQL_URL, search),
    ([, , variables]) =>
      apiGraphQL("PendleMarket", {
        query: PENDLE_MARKET_QUERY,
        variables,
      }),
    {
      refreshInterval: REFRESH_INTERVALS.API_PAGE_MS,
      dedupingInterval: REFRESH_INTERVALS.API_DEDUPE_MS,
      revalidateOnFocus: false,
    },
  );

  const rows = useMemo(() => {
    const prices = new Map(
      (data?.pendleMarketPage?.latestPrices || []).map((price) => [
        String(price.assetAddress || "").toLowerCase(),
        price,
      ]),
    );
    return (data?.pendleMarketPage?.assets || []).map((asset) => ({
      ...asset,
      price: prices.get(String(asset.assetAddress || "").toLowerCase()) || null,
    }));
  }, [data]);

  const marketAddress = data?.pendleMarketPage?.marketAddress || rows[0]?.marketAddress || search;

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono selection:bg-pink-500/30">
      <main className="max-w-[1200px] mx-auto px-6 pb-12">
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => navigate(-1)}
            className="p-2 border border-white/10 hover:bg-white/5 transition-colors"
            aria-label="Go back"
          >
            <ArrowLeft size={16} className="text-gray-400" />
          </button>
          <div className="min-w-0">
            <h1 className="text-2xl font-medium tracking-tight text-white">
              Pendle Market
            </h1>
            <p className="text-xs text-gray-500 uppercase tracking-widest truncate">
              {marketAddress}
            </p>
          </div>
        </div>

        <div className="border border-white/10 bg-[#0a0a0a] relative overflow-hidden">
          {isLoading && (
            <div className="absolute inset-0 bg-black/50 backdrop-blur-sm z-10 flex flex-col items-center justify-center">
              <Loader2 className="w-8 h-8 text-cyan-500 animate-spin mb-2" />
              <span className="text-sm uppercase tracking-widest text-white">Loading Pendle Assets</span>
            </div>
          )}

          {error ? (
            <div className="p-6 text-sm text-red-300">
              Pendle data unavailable: {error.message}
            </div>
          ) : rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-white/[0.03] text-gray-500 uppercase tracking-widest text-xs">
                  <tr>
                    <th className="text-left p-4 font-normal">Asset</th>
                    <th className="text-left p-4 font-normal">Type</th>
                    <th className="text-right p-4 font-normal">Price</th>
                    <th className="text-right p-4 font-normal">Expiry</th>
                    <th className="text-right p-4 font-normal">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {rows.map((row) => (
                    <tr key={row.assetAddress} className="hover:bg-white/[0.02]">
                      <td className="p-4">
                        <div className="text-white">{row.symbol}</div>
                        <div className="text-xs text-gray-600 truncate max-w-[360px]">
                          {row.assetAddress}
                        </div>
                      </td>
                      <td className="p-4 text-gray-400">{row.assetType}</td>
                      <td className="p-4 text-right text-cyan-300">
                        {row.price ? formatUsd(row.price.priceUsd) : "n/a"}
                      </td>
                      <td className="p-4 text-right text-gray-400">{formatDate(row.expiry)}</td>
                      <td className="p-4 text-right">
                        <span className={row.active ? "text-emerald-300" : "text-gray-500"}>
                          {row.active ? "Active" : row.matured ? "Matured" : "Inactive"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-6 text-sm text-gray-500">
              No Pendle PT/YT assets found for this market.
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
