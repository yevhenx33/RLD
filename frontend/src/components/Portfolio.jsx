import React, { useState } from "react";
import { useWallet } from "../context/WalletContext";
import { useUserNFTs } from "../hooks/useUserNFTs";
import {
  Wallet,
  Timer,
  CheckCircle,
  TrendingUp,
  AlertCircle,
  LayoutGrid,
  List,
  ArrowUpDown,
  Filter,
  ChevronDown,
  Check,
  ArrowUpRight,
} from "lucide-react";

// --- UTILS ---
const formatDate = (isoString) => {
  if (!isoString) return "N/A";
  return new Date(isoString).toLocaleDateString("en-GB", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

const calculateRemainingDays = (maturityDate) => {
  const now = new Date();
  const maturity = new Date(maturityDate);
  const diffTime = maturity - now;
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  return diffDays > 0 ? diffDays : 0;
};

// --- HELPER COMPONENTS ---
function ControlCell({ label, children, className = "" }) {
  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      <span className="text-[11px] text-gray-500 uppercase tracking-[0.2em] font-bold">
        {label}
      </span>
      <div className="flex items-center w-full flex-wrap gap-y-2">
        {children}
      </div>
    </div>
  );
}

function FilterDropdown({ label, options, selected, onChange }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = React.useRef(null);

  // Close on click outside
  React.useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative w-full" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`
                    w-full h-[30px] border border-white/20 bg-black flex items-center justify-between px-3 
                    text-xs font-mono text-white focus:outline-none uppercase tracking-widest 
                    hover:border-white transition-colors
                    ${isOpen ? "border-white" : ""}
                `}
      >
        <div className="flex items-center gap-2 overflow-hidden">
          <span>
            {selected === "ALL"
              ? label
              : options.find((o) => o.value === selected)?.label || selected}
          </span>
        </div>
        <ChevronDown
          size={14}
          className={`transition-transform duration-200 ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-[#0a0a0a] border border-white/20 z-50 flex flex-col shadow-xl">
          <div className="max-h-[300px] overflow-y-auto p-1 space-y-0.5 custom-scrollbar">
            {/* ALL OPTION */}
            <button
              onClick={() => {
                onChange("ALL");
                setIsOpen(false);
              }}
              className={`
                        w-full flex items-center gap-3 px-3 py-2.5 text-xs text-left uppercase tracking-widest transition-colors
                        ${
                          selected === "ALL"
                            ? "bg-cyan-500/10 text-cyan-400"
                            : "text-gray-500 hover:bg-white/5 hover:text-gray-300"
                        }
                        border-b border-white/5 mb-1
                    `}
            >
              <div
                className={`
                        w-3.5 h-3.5 border flex items-center justify-center transition-colors
                        ${
                          selected === "ALL"
                            ? "bg-cyan-500 border-cyan-500"
                            : "border-white/20 group-hover:border-white/40"
                        }
                    `}
              >
                {selected === "ALL" && (
                  <Check size={10} className="text-black stroke-[3]" />
                )}
              </div>
              ALL
            </button>

            {options.map((opt) => {
              const isSelected = selected === opt.value;
              return (
                <button
                  key={opt.value}
                  onClick={() => {
                    onChange(opt.value);
                    setIsOpen(false);
                  }}
                  className={`
                                        w-full flex items-center gap-3 px-3 py-2.5 text-xs text-left uppercase tracking-widest transition-colors
                                        ${
                                          isSelected
                                            ? "bg-cyan-500/10 text-cyan-400"
                                            : "text-gray-500 hover:bg-white/5 hover:text-gray-300"
                                        }
                                    `}
                >
                  <div
                    className={`
                                        w-3.5 h-3.5 border flex items-center justify-center transition-colors
                                        ${
                                          isSelected
                                            ? "bg-cyan-500 border-cyan-500"
                                            : "border-white/20 group-hover:border-white/40"
                                        }
                                    `}
                  >
                    {isSelected && (
                      <Check size={10} className="text-black stroke-[3]" />
                    )}
                  </div>
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// --- COMPONENTS ---
const BondCard = ({ nft }) => {
  const isMatured = nft.status === "MATURED";
  const daysRemaining = calculateRemainingDays(nft.maturityDate);

  return (
    <div className="group relative h-full bg-[#0a0a0a] border border-white/10 hover:border-white/20 transition-colors flex flex-col">
      {/* Header: Identity with distinct background */}
      <div className="bg-white/5 px-5 py-3 border-b border-white/5 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-white/20" />
          <span className="font-mono text-sm text-gray-200 font-medium">
            {nft.currency} Bond
          </span>
        </div>
        <span className="font-mono text-xs text-gray-500">#{nft.tokenId}</span>
      </div>

      {/* Body: Structured Data with Dividers */}
      <div className="flex-1 px-5 py-2 flex flex-col divide-y divide-white/5">
        {/* Row 1: Principal - The Anchor */}
        <div className="py-4">
          <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
            Principal
          </div>
          <div className="text-xl text-white font-mono font-light tracking-tight">
            {Number(nft.principal).toLocaleString()} {nft.currency}
          </div>
        </div>

        {/* Row 2: Yield & Return - High Value Info */}
        <div className="py-4 grid grid-cols-2 gap-4">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Fixed APY
            </div>
            <div className="text-base text-cyan-400 font-mono">
              {nft.rate.toFixed(2)}%
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Est. Return
            </div>
            <div className="text-base text-gray-300 font-mono">
              +
              {(
                (nft.principal * (nft.rate / 100) * (365 - daysRemaining)) /
                365
              ).toFixed(2)}
            </div>
          </div>
        </div>

        {/* Row 3: Timeline - Context */}
        <div className="py-4 mt-auto grid grid-cols-2 gap-4">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Maturity
            </div>
            <div className="text-sm text-gray-400 font-mono">
              {formatDate(nft.maturityDate)}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Status
            </div>
            <div
              className={`text-sm font-mono ${
                daysRemaining <= 30 && !isMatured
                  ? "text-yellow-500"
                  : "text-gray-400"
              }`}
            >
              {isMatured ? (
                <span className="text-green-500">Ready</span>
              ) : (
                <span>{daysRemaining} Days</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer: Action */}
      <div className="px-5 py-4 bg-[#050505] border-t border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div
            className={`w-1.5 h-1.5 rounded-full ${isMatured ? "bg-green-500" : "bg-cyan-500"}`}
          />
          <span
            className={`text-[10px] uppercase tracking-widest font-medium ${isMatured ? "text-green-500" : "text-cyan-500"}`}
          >
            {isMatured ? "Matured" : "Active"}
          </span>
        </div>

        <button
          disabled={!isMatured}
          className={`text-[10px] uppercase tracking-[0.15em] font-bold flex items-center gap-1 transition-colors
          ${
            isMatured
              ? "text-white hover:text-green-400 hover:underline decoration-green-500/50 underline-offset-4"
              : "text-gray-600 cursor-not-allowed"
          }`}
        >
          {isMatured ? "Claim Funds" : "Redeem Early"}
          {isMatured && <ArrowUpRight size={12} />}
        </button>
      </div>
    </div>
  );
};

const BondTable = ({ nfts, sortConfig, onSort }) => {
  const getSortIcon = (key) => {
    if (sortConfig.key !== key)
      return <ArrowUpDown size={12} className="opacity-30" />;
    return (
      <ArrowUpDown
        size={12}
        className={
          sortConfig.direction === "asc"
            ? "opacity-100 rotate-180"
            : "opacity-100"
        }
      />
    );
  };

  const HeaderCell = ({ label, sortKey, align = "center" }) => (
    <th
      className={`p-5 text-xs uppercase tracking-widest text-gray-500 font-bold cursor-pointer hover:text-white transition-colors group select-none text-${align}`}
      onClick={() => onSort(sortKey)}
    >
      <div
        className={`flex items-center gap-2 ${
          align === "right"
            ? "justify-end"
            : align === "center"
              ? "justify-center"
              : "justify-start"
        }`}
      >
        {/* Spacer for center alignment to balance the sort icon */}
        {align === "center" && <div className="w-[12px]" />}
        {label}
        {getSortIcon(sortKey)}
      </div>
    </th>
  );

  return (
    <div className="border border-white/10 bg-[#0a0a0a]">
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-white/10 bg-white/[0.02]">
              <HeaderCell label="Token ID" sortKey="tokenId" align="center" />
              <HeaderCell label="APY" sortKey="rate" align="center" />
              <HeaderCell
                label="Principal"
                sortKey="principal"
                align="center"
              />
              <HeaderCell label="Asset" sortKey="currency" align="center" />
              <HeaderCell
                label="Maturity"
                sortKey="maturityDate"
                align="center"
              />
              <HeaderCell
                label="Time Left"
                sortKey="maturityDate"
                align="center"
              />
              <HeaderCell label="Status" sortKey="status" align="center" />
              <th className="p-5 text-xs uppercase tracking-widest text-gray-500 font-bold text-center">
                Action
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {nfts.map((nft) => {
              const isMatured = nft.status === "MATURED";
              const daysRemaining = calculateRemainingDays(nft.maturityDate);
              return (
                <tr
                  key={nft.id}
                  className="hover:bg-white/[0.03] transition-colors group"
                >
                  <td className="p-5 text-sm font-mono text-white text-center">
                    #{nft.tokenId}
                  </td>
                  <td className="p-5 text-sm font-mono text-cyan-400 text-center">
                    {nft.rate.toFixed(2)}%
                  </td>
                  <td className="p-5 text-sm font-mono text-white text-center">
                    {nft.principal.toLocaleString()}
                  </td>
                  <td className="p-5 text-sm font-mono text-white font-bold text-center">
                    {nft.currency}
                  </td>
                  <td className="p-5 text-sm font-mono text-white text-center">
                    {formatDate(nft.maturityDate)}
                  </td>
                  <td className="p-5 text-sm font-mono text-center">
                    <div
                      className={`flex items-center justify-center gap-2 ${
                        daysRemaining <= 30 && !isMatured
                          ? "text-yellow-500"
                          : "text-gray-400"
                      }`}
                    >
                      {isMatured ? (
                        <CheckCircle size={14} className="text-green-500" />
                      ) : (
                        <span className="flex items-center gap-1">
                          {daysRemaining}d
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="p-5 text-center">
                    <span
                      className={`text-[10px] font-bold uppercase tracking-widest px-2 py-1 border ${
                        isMatured
                          ? "text-green-500 border-green-500/30 bg-green-500/10"
                          : "text-blue-400 border-blue-400/30 bg-blue-400/10"
                      }`}
                    >
                      {isMatured ? "MATURED" : "ACTIVE"}
                    </span>
                  </td>
                  <td className="p-5 text-center">
                    <button
                      disabled={!isMatured}
                      className={`text-[10px] font-bold uppercase tracking-widest px-4 py-2 border transition-all ${
                        isMatured
                          ? "border-green-500 text-green-500 hover:bg-green-500 hover:text-white"
                          : "border-white/10 text-gray-600 cursor-not-allowed"
                      }`}
                    >
                      {isMatured ? "CLAIM" : "REDEEM NOW"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const Portfolio = () => {
  const { account, connectWallet } = useWallet();
  const { nfts, loading, error } = useUserNFTs(account);
  const [viewMode, setViewMode] = React.useState("TABLE"); // 'GRID' or 'TABLE'

  // Sorting & Filtering State
  const [sortConfig, setSortConfig] = React.useState({
    key: "maturityDate",
    direction: "asc",
  });
  const [filters, setFilters] = React.useState({ status: "ALL", asset: "ALL" });
  const [useStateTrigger, setUseStateTrigger] = React.useState(0);

  const handleSort = (key) => {
    let direction = "asc";
    if (sortConfig.key === key && sortConfig.direction === "asc") {
      direction = "desc";
    }
    setSortConfig({ key, direction });
  };

  const processedData = React.useMemo(() => {
    if (!nfts) return [];

    let result = [...nfts];

    // Filter
    if (filters.status !== "ALL") {
      result = result.filter((n) => n.status === filters.status);
    }
    if (filters.asset !== "ALL") {
      result = result.filter((n) => n.currency === filters.asset);
    }

    // Sort
    if (sortConfig.key) {
      result.sort((a, b) => {
        let valA = a[sortConfig.key];
        let valB = b[sortConfig.key];

        // Handle strings (case insensitive)
        if (typeof valA === "string") valA = valA.toLowerCase();
        if (typeof valB === "string") valB = valB.toLowerCase();

        // Handle numerics explicitly if mixed types (shouldn't happen here but safe)
        if (sortConfig.key === "tokenId") {
          valA = Number(valA);
          valB = Number(valB);
        }

        if (valA < valB) return sortConfig.direction === "asc" ? -1 : 1;
        if (valA > valB) return sortConfig.direction === "asc" ? 1 : -1;
        return 0;
      });
    }

    return result;
  }, [nfts, sortConfig, filters]);

  // Derived Options
  const assets = React.useMemo(() => {
    if (!nfts) return [];
    const set = new Set(nfts.map((n) => n.currency));
    return ["ALL", ...Array.from(set)];
  }, [nfts]);

  if (!account) {
    return (
      <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono flex flex-col items-center justify-center p-6">
        <div className="max-w-md w-full border border-white/10 bg-[#080808] p-8 text-center">
          <Wallet size={48} className="mx-auto text-gray-600 mb-6" />
          <h2 className="text-xl font-bold uppercase tracking-widest mb-4">
            Wallet Disconnected
          </h2>
          <p className="text-gray-500 text-sm mb-8 leading-relaxed">
            Connect your wallet to view your active bond holdings and portfolio
            performance.
          </p>
          <button
            onClick={connectWallet}
            className="w-full bg-white text-black font-bold uppercase tracking-widest py-3 hover:bg-gray-200 transition-colors"
          >
            Connect Wallet
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono px-6 pt-4 pb-12">
      <div className="max-w-[1800px] mx-auto">
        {/* Header Section */}
        <div className="mb-8">
          <div className="flex flex-col xl:flex-row justify-between items-start xl:items-end gap-6">
            <div>
              <h1 className="text-4xl font-light mb-2 tracking-tight">
                PORTFOLIO
              </h1>
              <p className="text-gray-500 text-xs uppercase tracking-widest">
                Manage your Fixed-Yield Bond Positions
              </p>
            </div>

            {/* View Toggle */}
            <div className="flex bg-[#080808] border border-white/10">
              <button
                onClick={() => setViewMode("GRID")}
                className={`p-2 transition-colors ${viewMode === "GRID" ? "bg-white text-black" : "text-gray-500 hover:text-white"}`}
                title="Grid View"
              >
                <LayoutGrid size={18} />
              </button>
              <div className="w-[1px] bg-white/10"></div>
              <button
                onClick={() => setViewMode("TABLE")}
                className={`p-2 transition-colors ${viewMode === "TABLE" ? "bg-white text-black" : "text-gray-500 hover:text-white"}`}
                title="Table View"
              >
                <List size={18} />
              </button>
            </div>
          </div>
        </div>

        {/* Totals Section */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-6 mb-6 border px-6 py-4 border-white/10 bg-[#080808] items-center">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Total Assets
            </div>
            <div className="text-xl text-white font-mono">
              {processedData.length}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Active Value
            </div>
            <div className="text-xl text-white font-mono">
              $
              {processedData
                .filter((n) => n.status === "ACTIVE")
                .reduce((acc, curr) => acc + curr.principal, 0)
                .toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Matured Value
            </div>
            <div className="text-xl text-green-500 font-mono">
              $
              {processedData
                .filter((n) => n.status === "MATURED")
                .reduce((acc, curr) => acc + curr.principal, 0)
                .toLocaleString()}
            </div>
          </div>
        </div>

        {/* Controls Bar */}
        <div className="mb-8 flex flex-wrap items-start gap-6">
          <ControlCell label="STATUS" className="w-[200px] xl:w-[220px]">
            <FilterDropdown
              label="ALL"
              options={[
                { label: "Active", value: "ACTIVE" },
                { label: "Matured", value: "MATURED" },
              ]}
              selected={filters.status}
              onChange={(val) =>
                setFilters((prev) => ({ ...prev, status: val }))
              }
            />
          </ControlCell>
          <ControlCell label="ASSETS" className="w-[200px] xl:w-[220px]">
            <FilterDropdown
              label="ALL"
              options={assets.map((a) => ({ label: a, value: a }))}
              selected={filters.asset}
              onChange={(val) =>
                setFilters((prev) => ({ ...prev, asset: val }))
              }
            />
          </ControlCell>

          {/* Grid Sort (Visible only in Grid Mode) */}
          {viewMode === "GRID" && (
            <ControlCell label="SORT" className="w-[200px] xl:w-[220px]">
              <div className="flex items-center gap-2 w-full">
                <div className="flex-1">
                  <FilterDropdown
                    label="SORT BY"
                    options={[
                      { label: "Maturity", value: "maturityDate" },
                      { label: "Principal", value: "principal" },
                      { label: "APY", value: "rate" },
                      { label: "ID", value: "tokenId" },
                    ]}
                    selected={sortConfig.key}
                    onChange={(val) => handleSort(val)}
                  />
                </div>
                <button
                  onClick={() =>
                    setSortConfig((prev) => ({
                      ...prev,
                      direction: prev.direction === "asc" ? "desc" : "asc",
                    }))
                  }
                  className="h-[30px] w-[30px] border border-white/20 bg-black flex items-center justify-center text-white hover:border-white transition-colors"
                >
                  <ArrowUpDown
                    size={14}
                    className={
                      sortConfig.direction === "asc" ? "rotate-180" : ""
                    }
                  />
                </button>
              </div>
            </ControlCell>
          )}
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center h-64 animate-pulse">
            <span className="text-xs uppercase tracking-widest text-gray-500">
              Loading Assets...
            </span>
          </div>
        ) : error ? (
          <div className="border border-red-900/50 bg-red-900/10 p-6 flex items-center gap-4 text-red-500">
            <AlertCircle size={20} />
            <span className="text-xs uppercase tracking-widest">
              Error fetching portfolio data
            </span>
          </div>
        ) : processedData.length === 0 ? (
          <div className="border border-white/10 border-dashed p-12 text-center text-gray-500">
            <p className="text-sm uppercase tracking-widest">
              No Asset Matches Filter
            </p>
          </div>
        ) : viewMode === "GRID" ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
            {processedData.map((nft) => (
              <BondCard key={nft.id} nft={nft} />
            ))}
          </div>
        ) : (
          <BondTable
            nfts={processedData}
            sortConfig={sortConfig}
            onSort={handleSort}
          />
        )}
      </div>
    </div>
  );
};

export default Portfolio;
