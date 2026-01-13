import React, { useState } from 'react';
import { ChevronDown } from "lucide-react";

// Mobile Dropdown Component
function MobileDropdown({ value, options, onChange }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative w-full md:hidden">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full h-[30px] border border-white/20 bg-black flex items-center justify-between px-3 text-xs font-mono text-white focus:outline-none uppercase tracking-widest hover:border-white transition-colors"
      >
        <span>{value}</span>
        <ChevronDown size={14} className={`transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen && (
        <>
        <div 
          className="fixed inset-0 z-30 bg-transparent"
          onClick={() => setIsOpen(false)}
        />
        <div className="absolute top-full left-0 w-full mt-1 bg-[#0a0a0a] border border-white/20 z-40 max-h-[200px] overflow-y-auto shadow-xl">
          {options.map((opt) => (
            <button
              key={opt.value}
              onClick={() => {
                onChange(opt.value);
                setIsOpen(false);
              }}
              className={`w-full text-left px-4 py-3 text-xs font-mono uppercase tracking-widest transition-colors hover:bg-white/5 border-b border-white/5 last:border-b-0 ${
                value === opt.label ? "text-cyan-400 bg-white/5" : "text-gray-400"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        </>
      )}
    </div>
  );
}

export default MobileDropdown;
