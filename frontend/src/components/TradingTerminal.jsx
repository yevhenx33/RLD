import React from "react";

// Helper for summary rows
export const SummaryRow = ({ label, value, valueColor = "text-white" }) => (
  <div className="flex justify-between items-center text-[12px]">
    <span className="text-gray-500 uppercase">{label}</span>
    <span className={`font-mono ${valueColor}`}>{value}</span>
  </div>
);

// Helper for input groups
export const InputGroup = ({ label, subLabel, value, onChange, suffix, type = "number", placeholder = "0.00" }) => (
  <div className="space-y-2">
    <div className="flex justify-between text-[12px] uppercase tracking-widest font-bold text-gray-500">
      <span>{label}</span>
      <span>{subLabel}</span>
    </div>
    <div className="relative group">
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent border-b border-white/20 text-sm font-mono text-white py-2 focus:outline-none focus:border-white transition-colors placeholder-gray-800 rounded-none"
        placeholder={placeholder}
      />
      {suffix && (
        <span className="absolute right-0 top-2 text-sm text-gray-600">
          {suffix}
        </span>
      )}
    </div>
  </div>
);

const TradingTerminal = ({
  // Header
  title,
  Icon, // Component
  subTitle,

  // Tabs (optional)
  tabs = [], // { id, label, onClick, isActive, color }
  
  // Content
  children,

  // Action Button
  actionButton = {
    label: "ACTION",
    onClick: () => {},
    disabled: false,
    variant: "cyan", // cyan, pink
    connectWallet: null, // If provided, shows "Connect" instead when no account
  },
  
  account, // Passed for connect wallet logic if needed
  connectWallet, // Passed for connect wallet logic
  footer, // Optional footer content
}) => {
  return (
    <div className="xl:col-span-3 border border-white/10 bg-[#080808] flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center h-[50px]">
        <h3 className="text-xs font-bold tracking-widest text-white uppercase flex items-center gap-2">
          {Icon && <Icon size={15} className="text-gray-500" />} {title}
        </h3>
        {subTitle && (
          <span className="text-[12px] text-gray-600 uppercase tracking-widest">
            {subTitle}
          </span>
        )}
      </div>

      {/* Tabs */}
       {tabs.length > 0 && (
        <div className="grid grid-cols-2 border-b border-white/10">
          {tabs.map((tab) => (
             <button
               key={tab.id}
               onClick={tab.onClick}
               className={`py-3 text-[12px] font-bold tracking-widest uppercase transition-colors focus:outline-none rounded-none ${
                 tab.isActive
                   ? tab.activeClass || "bg-white text-black"
                   : "bg-[#080808] text-gray-600 hover:text-gray-400 hover:bg-white/5"
               }`}
             >
               {tab.label}
             </button>
          ))}
        </div>
      )}


      {/* Main Content Area */}
      <div className="flex-1 flex flex-col p-6 gap-6">
        {children}

        {/* Action Button */}
        <div className="mt-auto">
          {account ? (
            <button
              onClick={actionButton.onClick}
              disabled={actionButton.disabled}
              className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none ${
                actionButton.variant === "pink"
                  ? "bg-pink-500 text-black hover:bg-pink-400"
                  : "bg-cyan-500 text-black hover:bg-cyan-400"
              } ${actionButton.disabled ? "opacity-50 cursor-not-allowed" : "hover:opacity-90"}`}
            >
              {actionButton.label}
            </button>
          ) : (
            <button
              onClick={actionButton.connectWallet || connectWallet}
              className="w-full py-4 border border-white/20 text-xs font-bold tracking-[0.2em] uppercase text-gray-400 hover:text-white hover:border-white transition-all focus:outline-none rounded-none"
            >
              Connect to Trade
            </button>
          )}
        </div>
      </div>
      
      {/* Optional Footer */}
      {footer}
    </div>
  );
};

export default TradingTerminal;

